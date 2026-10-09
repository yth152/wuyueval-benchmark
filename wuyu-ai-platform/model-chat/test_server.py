import asyncio
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import server
from streaming import ThinkSplitter, sse_data


class ParsingTests(unittest.IsolatedAsyncioTestCase):
    def test_every_tag_boundary(self):
        text = ' \n<think>先计算 17×23。</think>391。'
        for width in range(1, len(text) + 1):
            parser = ThinkSplitter()
            events = []
            for index in range(0, len(text), width):
                events.extend(parser.feed(text[index:index + width]))
            events.extend(parser.finish())
            self.assertEqual(''.join(t for k, t in events if k == 'reasoning'), '先计算 17×23。')
            self.assertEqual(''.join(t for k, t in events if k == 'content'), '391。')

    def test_plain_content_is_never_inferred_as_thought(self):
        parser = ThinkSplitter()
        events = parser.feed('需要分析一下。代码中的 <think> 是一个标签。') + parser.finish()
        self.assertEqual(events, [('content', '需要分析一下。代码中的 <think> 是一个标签。')])

    def test_unclosed_tag_remains_reasoning(self):
        parser = ThinkSplitter()
        events = parser.feed('<think>未完成') + parser.finish()
        self.assertEqual(events, [('reasoning', '未完成')])
        self.assertEqual(parser.mode, 'reasoning')

    async def test_sse_multiline_comments_and_unterminated_final_event(self):
        async def lines():
            for line in [': heartbeat', 'event: message', 'data: {', 'data: "ok": true}', '', 'data: [DONE]']:
                yield line
        self.assertEqual([x async for x in sse_data(lines())], ['{\n"ok": true}', '[DONE]'])


class FakeResponse:
    def __init__(self, chunks, status=200):
        self.chunks, self.status_code = chunks, status
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def aiter_bytes(self):
        yield json.dumps(self.chunks).encode()
    async def aiter_lines(self):
        for chunk in self.chunks:
            yield 'data: ' + (chunk if isinstance(chunk, str) else json.dumps(chunk))
            yield ''


class FakeClient:
    chunks = []
    status = 200
    payload = None
    def __init__(self, *_, **__): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    def stream(self, *_, **kwargs):
        FakeClient.payload = kwargs['json']
        return FakeResponse(self.chunks, self.status)


def chunk(delta, finish=None):
    return {'model': 'actual-test-model', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def run_stream(self, chunks, status=200, provider='team'):
        FakeClient.chunks, FakeClient.status = chunks, status
        body = server.ChatRequest(provider=provider, messages=[{'role': 'user', 'content': '测试'}])
        server.active[provider] = 1
        with patch.object(server.httpx, 'AsyncClient', FakeClient):
            result = [json.loads(x[6:]) async for x in server.stream_reply(body, 'fake-test-key')]
        self.assertEqual(server.active[provider], 0)
        return result

    async def test_both_reasoning_aliases_and_same_chunk_answer(self):
        for field in ('reasoning', 'reasoning_content'):
            events = await self.run_stream([chunk({field: '模型原始推理', 'content': '答案'}, 'stop'),
                                           {'usage': {'completion_tokens': 6}, 'choices': []}, '[DONE]'])
            self.assertTrue(events[-1]['complete'])
            self.assertEqual(events[-1]['model'], 'actual-test-model')
            self.assertEqual(events[-1]['usage']['completion_tokens'], 6)
            self.assertEqual(events[-1]['reasoning_sources'], [field])
            self.assertEqual([e['type'] for e in events], ['start', 'reasoning', 'content', 'done'])

    async def test_thinking_parameters_on_baseline(self):
        await self.run_stream([chunk({'content': '答案'}, 'stop'), '[DONE]'], provider='baseline')
        self.assertTrue(FakeClient.payload['enable_thinking'])
        self.assertTrue(FakeClient.payload['chat_template_kwargs']['enable_thinking'])

    async def test_missing_reasoning_is_reported_honestly(self):
        events = await self.run_stream([chunk({'content': '答案'}, 'stop'), '[DONE]'])
        self.assertFalse(events[-1]['has_reasoning'])
        self.assertTrue(events[-1]['complete'])

    async def test_zero_token_stream_is_reported_as_empty_response_not_output_limit(self):
        events=await self.run_stream([chunk({'content':''}),
            {'choices':[],'usage':{'prompt_tokens':1200,'completion_tokens':0}},'[DONE]'])
        self.assertEqual(events[-1]['error_kind'],'empty_model_response')
        self.assertEqual(events[-1]['usage']['completion_tokens'],0)
        self.assertNotIn('输出上限',events[-1]['message'])

    async def test_truncation_and_premature_eof_are_incomplete(self):
        events = await self.run_stream([chunk({'content': '部分答案'}, 'length'), '[DONE]'])
        self.assertFalse(events[-1]['complete'])
        events = await self.run_stream([chunk({'content': '部分答案'})])
        self.assertEqual(events[-1]['type'], 'error')

    async def test_upstream_errors_are_safe(self):
        events = await self.run_stream([], status=401)
        self.assertEqual(events[-1]['upstream_status'], 401)
        self.assertNotIn('fake-test-key', json.dumps(events))

    async def test_cancellation_releases_slot(self):
        body = server.ChatRequest(messages=[{'role': 'user', 'content': '测试'}])
        server.active['team'] = 1
        generator = server.stream_reply(body, 'fake-test-key')
        await anext(generator)
        await generator.aclose()
        self.assertEqual(server.active['team'], 0)


class BoundaryTests(unittest.TestCase):
    def setUp(self): self.client = TestClient(server.app, base_url='http://127.0.0.1:8910')
    def test_cross_origin_and_dns_rebinding_are_blocked(self):
        self.assertEqual(self.client.get('/api/health', headers={'Origin': 'https://example.com'}).status_code, 403)
        self.assertEqual(self.client.get('/api/health', headers={'Host': 'evil.test:8910'}).status_code, 403)
        self.assertEqual(self.client.get('/api/health').status_code, 200)
    def test_runtime_is_not_served(self):
        self.assertEqual(self.client.get('/runtime/team.dpapi').status_code, 404)
        self.assertEqual(self.client.get('/assets/../runtime/team.dpapi').status_code, 404)
    def test_arbitrary_provider_or_assistant_last_is_rejected(self):
        self.assertEqual(self.client.post('/api/chat', json={'provider': 'http://evil.test', 'messages': [{'role': 'user', 'content': 'x'}]}).status_code, 422)
        self.assertEqual(self.client.post('/api/chat', json={'messages': [{'role': 'assistant', 'content': 'x'}]}).status_code, 422)
    def test_provider_endpoint_exposes_no_credentials(self):
        with patch.object(server, 'configured', return_value=True):
            values = self.client.get('/api/providers').json()
        for value in values:
            self.assertEqual(set(value), {'id', 'label', 'model', 'configured'})

    def test_export_roundtrip_and_path_isolation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, 'ROOT', Path(directory)):
            response = self.client.post('/api/export', json={'text': '# 测试\n\n问题与思考'} )
            self.assertEqual(response.status_code, 200)
            saved = response.json()
            self.assertEqual(Path(saved['path']).read_text(encoding='utf-8-sig'), '# 测试\n\n问题与思考')
            download = self.client.get(saved['url'])
            self.assertEqual(download.status_code, 200)
            self.assertIn('attachment', download.headers['content-disposition'])
            self.assertEqual(self.client.get('/api/exports/team.dpapi').status_code, 404)


if __name__ == '__main__': unittest.main()
