"""Fit evidence into the deployed context window without re-running searches."""
import copy
import json
import logging
import math
import re

import httpx

DEFAULT_CONTEXT = 32768
SAFETY_TOKENS = 512
IMAGE_TOKEN_RESERVE = 6144  # Uploads are bounded to a 2048-pixel longest edge.
logger = logging.getLogger('model_chat.context')


class ContextCapacityError(ValueError):
    pass


def compact_tool_result(name, batches):
    """Full search results go to the UI; the model receives each source once."""
    summaries, sources = [], {}
    for batch in batches:
        summaries.append({k: v for k, v in batch.items() if k in {
            'language', 'query', 'ok', 'error', 'date_window', 'date_policy',
            'note', 'returned_count', 'excluded_by_date', 'cached'}})
        summaries[-1]['source_ids'] = [s['source_id'] for s in batch.get('sources', [])]
        for source in batch.get('sources', []):
            sources.setdefault(source['source_id'], {k: v for k, v in source.items() if k in {
                'source_id', 'title', 'url', 'snippet', 'date', 'authors', 'type', 'year_status'}})
    return json.dumps({'tool': name, 'bilingual_results': summaries,
                       'sources': list(sources.values())}, ensure_ascii=False, separators=(',', ':'))


def estimated_tokens(payload):
    """Conservative fallback only; the team endpoint uses its own tokenizer."""
    images = 0
    messages = copy.deepcopy(payload['messages'])
    for message in messages:
        if isinstance(message.get('content'), list):
            images += sum(part.get('type') == 'image_url' for part in message['content'])
            message['content'] = [part for part in message['content'] if part.get('type') != 'image_url']
    text = json.dumps({'messages': messages, 'tools': payload.get('tools', [])}, ensure_ascii=False)
    ascii_chars = sum(ord(c) < 128 for c in text)
    return math.ceil(ascii_chars / 3 + (len(text) - ascii_chars) * 1.5) + images * IMAGE_TOKEN_RESERVE + 256


async def measure_context(client, provider, key, payload):
    # On the deployed service, /tokenize before the first image chat can leave
    # an invalid multimodal cache entry: later chats return HTTP 500 / zero tokens.
    # Never send image-bearing turns (including historical images) to that route.
    if any(isinstance(m.get('content'), list) and any(
            p.get('type') == 'image_url' for p in m['content']) for m in payload['messages']):
        return estimated_tokens(payload), provider.get('context_window', DEFAULT_CONTEXT), 'multimodal_estimate'
    # This route was verified on the deployed vLLM service. Other providers use
    # the conservative estimate and the bounded context-error recovery below.
    if provider.get('tokenize_url'):
        try:
            response = await client.post(provider['tokenize_url'],
                headers={'Authorization': 'Bearer ' + key}, timeout=10,
                json={k: v for k, v in payload.items() if k in {
                    'model', 'messages', 'tools', 'chat_template_kwargs'}} | {'add_generation_prompt': True})
            if response.status_code == 200:
                data = response.json()
                count, limit = data.get('count'), data.get('max_model_len')
                if isinstance(count, int) and count > 0 and isinstance(limit, int) and limit >= 2048:
                    return count, limit, 'tokenizer'
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass
    return estimated_tokens(payload), provider.get('context_window', DEFAULT_CONTEXT), 'estimate'


def trim_evidence(messages, snippet_limit):
    changed = False
    seen = set()
    for message in messages:
        if message['role'] != 'tool':
            continue
        try:
            data = json.loads(message['content'])
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get('sources'), list):
            continue
        # Custom query tools return bounded text in `data`. Keep full records in
        # the UI, but progressively shorten this excerpt alongside search snippets.
        data_limit = snippet_limit * 4
        if isinstance(data.get('data'), str) and len(data['data']) > data_limit:
            data['data'] = data['data'][:data_limit] + '…'
            data['data_truncated_for_context'] = True
            changed = True
        unique = []
        for source in data['sources']:
            if not isinstance(source, dict):
                continue
            identity = (source.get('source_id'), source.get('snippet'))
            if identity in seen:
                changed = True
                continue
            seen.add(identity)
            source = dict(source)
            for field, maximum in (('snippet', snippet_limit), ('title', 180)):
                if isinstance(source.get(field), str) and len(source[field]) > maximum:
                    source[field] = source[field][:maximum] + '…'
                    source['excerpt_truncated'] = True
                    changed = True
            # Citation IDs resolve to the original URL in the app. Very long
            # tracking URLs add no useful evidence to the model context.
            if len(source.get('url', '')) > 400:
                source.pop('url')
                changed = True
            unique.append(source)
        data['sources'] = unique
        message['content'] = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return changed


def drop_oldest_pair(messages):
    # Only remove complete user/assistant history before the current question.
    users = [i for i, m in enumerate(messages) if m['role'] == 'user']
    if len(users) < 2:
        return False
    first, second = users[:2]
    if second != first + 2 or messages[first + 1]['role'] != 'assistant' or messages[first + 1].get('tool_calls'):
        return False
    del messages[first:second]
    return True


async def fit_context(client, provider, key, payload, *, recovery=False, reported_limit=None):
    payload = copy.deepcopy(payload)
    messages = payload['messages']
    changes = set()
    for message in messages:
        if message.get('tool_calls'):
            # Keep tool arguments and results. Prior reasoning remains in the UI
            # and need not occupy the next model request.
            message.pop('reasoning', None)
            message.pop('reasoning_content', None)
    if recovery:
        if trim_evidence(messages, 240):
            changes.add('evidence')
        while drop_oldest_pair(messages):
            changes.add('history')
    requested_output = payload['max_tokens']
    margin = 2048 if recovery else SAFETY_TOKENS

    async def measure():
        count, limit, method = await measure_context(client, provider, key, payload)
        if reported_limit:
            limit = min(limit, reported_limit)
        return count, limit, method

    count, limit, method = await measure()
    # First shorten excerpts, then retire old dialogue, keeping the current
    # question and all source IDs intact. Never truncate arbitrary JSON/history.
    for snippet_limit in (900, 500):
        if count + requested_output + margin <= limit:
            break
        if trim_evidence(messages, snippet_limit):
            changes.add('evidence')
            count, limit, method = await measure()
    while count + requested_output + margin > limit and drop_oldest_pair(messages):
        changes.add('history')
        count, limit, method = await measure()
    # The formal platform supplies a request-local callback that removes only
    # its own structured memory prefixes. Legacy/custom clients remain intact.
    trim_memory = provider.get('trim_history')
    while count + requested_output + margin > limit and trim_memory and trim_memory(messages):
        changes.add('memory')
        count, limit, method = await measure()
    for snippet_limit in (240, 100):
        if count + requested_output + margin <= limit:
            break
        if trim_evidence(messages, snippet_limit):
            changes.add('evidence')
            count, limit, method = await measure()
    available = limit - count - margin
    if available < min(1024, requested_output):
        raise ContextCapacityError('本轮问题、附件和必要证据仍超过模型上下文容量。请拆分附件或缩小问题范围后重试；已取得的检索记录已保留。')
    payload['max_tokens'] = min(requested_output, available)
    if payload['max_tokens'] < requested_output:
        changes.add('output')
    return payload, {'input_tokens': count, 'context_window': limit,
                     'max_output_tokens': payload['max_tokens'], 'count_method': method,
                     'adjustments': sorted(changes), 'recovery': recovery}


async def upstream_problem(response):
    # Never forward or log raw upstream bodies: they can contain prompt text.
    raw = bytearray()
    async for block in response.aiter_bytes():
        raw.extend(block[:16384 - len(raw)])
        if len(raw) >= 16384:
            break
    text = raw.decode('utf-8', errors='replace').lower()
    context = any(term in text for term in ('maximum context length', 'context_length_exceeded',
        'context window', 'max_model_len', 'too many tokens', 'input_tokens'))
    match = re.search(r'maximum context length\D{0,20}(\d{4,7})', text)
    limit = int(match[1]) if match else None
    if context:
        return 'context_length', '问题、附件和检索结果超过模型上下文容量，请拆分问题或减少携带的历史。', limit
    if response.status_code == 400 and any(term in text for term in (
        'enable-auto-tool-choice', 'tool-call-parser', 'tool_choice', 'tool_calls', 'tools')):
        return 'tool_parameters', '模型服务拒绝了工具调用参数，请检查服务端工具协议配置。', None
    return 'upstream_error', {401: '模型接口认证失败，请检查本机凭据。',
        403: '模型接口拒绝访问。', 429: '模型接口繁忙或额度受限，请稍后再试。'}.get(
        response.status_code, f'模型接口返回错误（HTTP {response.status_code}），请检查服务状态或请求参数。'), None


def log_rejection(status, kind, round_index, search_calls, budget):
    logger.warning('upstream_rejection status=%s kind=%s round=%s searches=%s input_tokens=%s output_tokens=%s context=%s',
        status, kind, round_index + 1, search_calls, budget['input_tokens'],
        budget['max_output_tokens'], budget['context_window'])
