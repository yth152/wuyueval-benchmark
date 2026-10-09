import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

import agent_stream
import context_budget as cb
import server
from test_agent import QueuedClient, native_call, calls_round, answer_round, source
from test_server import FakeResponse


def evidence(count=80, length=1800):
    return [{'source_id': 'S' + str(i), 'title': '公开研究资料' + str(i),
             'url': 'https://example.org/' + str(i), 'date': '2026',
             'snippet': ('这是一段有来源的测试证据。' * 200)[:length]} for i in range(1, count + 1)]


def payload(sources=None):
    messages = [{'role': 'system', 'content': '按照用户问题回答并引用来源。'},
                {'role': 'user', 'content': '当前完整问题及附件不能被悄悄删除。'}]
    if sources:
        messages += [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_1'}],
                      'reasoning': '原始思考' * 10000},
                     {'role': 'tool', 'tool_call_id': 'call_1', 'content':
                      cb.compact_tool_result('web_search', [{'ok': True, 'sources': sources}])}]
    return {'model': 'test', 'messages': messages, 'max_tokens': 8192}


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_eighty_sources_fit_and_all_ids_and_originals_survive(self):
        p = payload(evidence())
        original = copy.deepcopy(p)
        fitted, meta = await cb.fit_context(None, {}, '', p)
        self.assertLessEqual(meta['input_tokens'] + fitted['max_tokens'] + cb.SAFETY_TOKENS, meta['context_window'])
        retained = json.loads(fitted['messages'][-1]['content'])['sources']
        self.assertEqual([s['source_id'] for s in retained], ['S' + str(i) for i in range(1, 81)])
        self.assertEqual(fitted['messages'][1], original['messages'][1])
        self.assertEqual(p, original)
        self.assertNotIn('reasoning', fitted['messages'][-2])
        self.assertIn('evidence', meta['adjustments'])

    async def test_bilingual_results_only_include_source_ids_not_duplicate_evidence(self):
        sources = evidence(2)
        result = json.loads(cb.compact_tool_result('web_search', [
            {'language': 'zh', 'ok': True, 'sources': sources},
            {'language': 'en', 'ok': True, 'sources': sources}]))
        self.assertEqual(len(result['sources']), 2)
        self.assertNotIn('sources', result['bilingual_results'][0])
        self.assertEqual(result['bilingual_results'][1]['source_ids'], ['S1', 'S2'])

    async def test_history_dropped_as_complete_pairs_only(self):
        p = payload()
        p['messages'][1:1] = [{'role': 'user', 'content': '旧的问题' * 3000},
                              {'role': 'assistant', 'content': '旧的回答' * 3000}]
        fitted, meta = await cb.fit_context(None, {}, '', p)
        self.assertEqual([m['role'] for m in fitted['messages']], ['system', 'user'])
        self.assertEqual(fitted['messages'][-1], p['messages'][-1])
        self.assertIn('history', meta['adjustments'])

    async def test_impossible_current_question_is_rejected_without_silent_truncation(self):
        p = payload()
        p['messages'][-1]['content'] = '很长的附件' * 7000
        with self.assertRaises(cb.ContextCapacityError):
            await cb.fit_context(None, {}, '', p)
        self.assertEqual(len(p['messages'][-1]['content']), 35000)

    async def test_output_reservation_is_reduced_when_only_that_can_fit(self):
        p = payload()
        with patch.object(cb, 'measure_context', AsyncMock(return_value=(27000, 32768, 'tokenizer'))):
            fitted, meta = await cb.fit_context(None, {}, '', p)
        self.assertEqual(fitted['max_tokens'], 5256)
        self.assertIn('output', meta['adjustments'])

    async def test_exact_tokenizer_and_fallback(self):
        class Client:
            async def post(self, *args, **kwargs):
                class Reply:
                    status_code = 200
                    def json(self): return {'count': 1234, 'max_model_len': 65536}
                return Reply()
        self.assertEqual(await cb.measure_context(Client(), {'tokenize_url': 'http://test/tokenize'}, '', payload()),
                         (1234, 65536, 'tokenizer'))
        self.assertEqual((await cb.measure_context(None, {}, '', payload()))[2], 'estimate')

    async def test_current_and_history_images_never_reach_tokenizer(self):
        client=AsyncMock()
        for history in (False, True):
            p=payload()
            p['messages'][-1]['content']=[{'type':'text','text':'看图'},
                {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,private-image'}}]
            if history:
                p['messages'] += [{'role':'assistant','content':'图片描述'},
                                  {'role':'user','content':'继续分析'}]
            fitted, meta=await cb.fit_context(client, {'tokenize_url':'http://test/tokenize'}, '', p)
            self.assertEqual(meta['count_method'],'multimodal_estimate')
            self.assertGreaterEqual(meta['input_tokens'], cb.IMAGE_TOKEN_RESERVE)
            self.assertEqual(fitted['messages'],p['messages'])
        client.post.assert_not_awaited()

    async def test_errors_classified_without_exposing_raw_body(self):
        cases = [('maximum context length is 32768 tokens. input_tokens=24577 secret-123', 'context_length'),
                 ('enable-auto-tool-choice and tool-call-parser required', 'tool_parameters'),
                 ('temperature not supported secret-123', 'upstream_error')]
        for message, kind in cases:
            result = await cb.upstream_problem(FakeResponse({'error': {'message': message}}, 400))
            self.assertEqual(result[0], kind)
            self.assertNotIn('secret-123', str(result))
        self.assertEqual((await cb.upstream_problem(FakeResponse({'error': {'message': cases[0][0]}}, 400)))[2], 32768)

    async def test_error_body_read_is_bounded(self):
        class Huge:
            status_code = 400
            async def aiter_bytes(self):
                for _ in range(1000):
                    yield b'x' * 20000
                    raise AssertionError('should stop after first bounded block')
        self.assertEqual((await cb.upstream_problem(Huge()))[0], 'upstream_error')


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, final_responses):
        QueuedClient.responses = [calls_round([native_call(i, query='方向' + str(i)) for i in range(4)])] + final_responses
        QueuedClient.payloads = []
        search = AsyncMock(return_value={'ok': True, 'sources': [{**source(), 'snippet': '完整检索摘要' * 300}]})
        body = server.ChatRequest(network_search=True, messages=[{'role': 'user', 'content': '问题'}])
        with patch.object(agent_stream.httpx, 'AsyncClient', QueuedClient), patch.object(agent_stream, 'search', search):
            events = [json.loads(event[6:]) async for event in agent_stream.run_agent(body, 'private-key')]
        return events, search, QueuedClient.payloads

    def overflow(self):
        return {'status': 400, 'body': {'error': {'message':
            "This model's maximum context length is 32768 tokens. input_tokens=24577 private-key"}}}

    async def test_eight_searches_then_context_retry_reuses_results_and_cites(self):
        events, search, requests = await self.exercise([self.overflow(), answer_round()])
        self.assertEqual(search.await_count, 8)
        self.assertTrue(events[-1]['complete'])
        self.assertEqual(events[-1]['model_calls'], 3)
        self.assertEqual(requests[1]['tool_choice'], 'none')
        self.assertEqual(requests[2]['tool_choice'], 'none')
        self.assertLess(len(json.dumps(requests[2])), len(json.dumps(requests[1])))
        self.assertEqual([s['source_id'] for e in events if e['type'] == 'citations' for s in e['sources']], ['S1'])
        self.assertNotIn('private-key', json.dumps(events))
        self.assertEqual(next(e for e in events if e['type'] == 'tool_result')['sources'][0]['snippet'], '完整检索摘要' * 300)

    async def test_context_retry_is_bounded_and_preserves_honest_error(self):
        events, search, requests = await self.exercise([self.overflow(), self.overflow()])
        self.assertEqual(search.await_count, 8)
        self.assertEqual(len(requests), 3)
        self.assertEqual(events[-1]['error_kind'], 'context_length')
        self.assertNotIn('工具配置', events[-1]['message'])

    async def test_unrelated_400_does_not_retry_or_blame_tool_parser(self):
        events, search, requests = await self.exercise([{'status': 400, 'body': {'error': 'temperature invalid'}}])
        self.assertEqual(len(requests), 2)
        self.assertEqual(events[-1]['error_kind'], 'upstream_error')
        self.assertNotIn('工具', events[-1]['message'])


if __name__ == '__main__': unittest.main()
