import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

import agent_stream
import server
import search_tools as st
from test_server import FakeResponse, chunk


def source(index=1, kind='webpage'):
    return {'title': f'来源{index}', 'url': f'https://example.org/{index}', 'snippet': f'证据{index}',
            'date': '2026-10-05', 'authors': [], 'type': kind}


def native_call(index=0, name='web_search', query='固废遥感'):
    return {'index': index, 'id': f'call_{index}', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps({'query_zh': '固废 '+query, 'query_en': 'solid waste '+query.encode('unicode_escape').decode()}, ensure_ascii=False)}}


def calls_round(calls):
    return [chunk({'tool_calls': calls}, 'tool_calls'), '[DONE]']


def answer_round(answer='结论[S1]。'):
    return [chunk({'content': answer}, 'stop'), '[DONE]']


class QueuedClient:
    responses = []
    payloads = []
    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def stream(self, *args, **kwargs):
        self.payloads.append(copy.deepcopy(kwargs['json']))
        response = self.responses.pop(0)
        return FakeResponse(response['body'], response['status']) if isinstance(response, dict) else FakeResponse(response)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, responses, search_result=None, network=True):
        QueuedClient.responses = copy.deepcopy(responses)
        QueuedClient.payloads = []
        mocked_search = AsyncMock(return_value=search_result or {'ok': True, 'sources': [source()]})
        body = server.ChatRequest(network_search=network, messages=[{'role': 'user', 'content': '测试问题'}])
        server.active['team'] = 1
        with patch.object(agent_stream.httpx, 'AsyncClient', QueuedClient), patch.object(agent_stream, 'search', mocked_search):
            events = [json.loads(event[6:]) async for event in server.stream_reply(body, 'test-secret')]
        self.assertEqual(server.active['team'], 0)
        return events, mocked_search, QueuedClient.payloads

    async def test_off_does_not_expose_or_execute_tools(self):
        events, search, payloads = await self.run_agent([answer_round('直接回答')], network=False)
        self.assertNotIn('tools', payloads[0]); self.assertNotIn('tool_choice', payloads[0])
        search.assert_not_awaited()
        self.assertEqual(events[-1]['search_calls'], 0)

    async def test_auto_may_choose_direct_answer(self):
        events, search, payloads = await self.run_agent([answer_round('391')])
        self.assertEqual(payloads[0]['tool_choice'], 'auto')
        self.assertEqual({t['function']['name'] for t in payloads[0]['tools']}, set(st.TOOL_SCOPES))
        search.assert_not_awaited()
        self.assertTrue(events[-1]['complete'])

    async def test_native_calls_return_tool_messages_before_continuation(self):
        first = calls_round([native_call(), native_call(1, 'scholar_search', 'solid waste')])
        events, search, payloads = await self.run_agent([first, answer_round()])
        self.assertEqual(search.await_count, 4)
        messages = payloads[1]['messages']
        self.assertEqual([m['role'] for m in messages[-3:]], ['assistant', 'tool', 'tool'])
        self.assertEqual(messages[-3]['tool_calls'][0]['id'], messages[-2]['tool_call_id'])
        self.assertEqual(json.loads(messages[-1]['content'])['sources'][0]['source_id'], 'S1')
        cited = next(e for e in events if e['type'] == 'citations')
        self.assertEqual([s['source_id'] for s in cited['sources']], ['S1'])
        self.assertEqual(events[-1]['model_calls'], 2)

    async def test_streaming_arguments_are_reassembled_before_execution(self):
        first = [chunk({'tool_calls': [{'index': 0, 'id': 'fragmented', 'function': {'name': 'web_', 'arguments': '{"query_zh":"固废","query_' }}]}),
                 chunk({'tool_calls': [{'index': 0, 'function': {'name': 'search', 'arguments': 'en":"solid waste"}'}}]}, 'tool_calls'), '[DONE]']
        events, search, _ = await self.run_agent([first, answer_round()])
        self.assertEqual(search.await_args_list[0].args, ('web_search', '固废', 'all')); self.assertEqual(search.await_args_list[1].args, ('web_search', 'solid waste', 'all'))
        self.assertTrue(events[-1]['complete'])

    async def test_truncated_call_never_executes(self):
        events, search, _ = await self.run_agent([[chunk({'tool_calls': [native_call()]}, 'length'), '[DONE]']])
        search.assert_not_awaited(); self.assertEqual(events[-1]['type'], 'error')

    async def test_off_rejects_unrequested_native_call(self):
        events, search, _ = await self.run_agent([calls_round([native_call()])], network=False)
        search.assert_not_awaited(); self.assertEqual(events[-1]['type'], 'error')

    async def test_plaintext_tool_markup_is_not_executed(self):
        events, search, _ = await self.run_agent([answer_round('<tool_call>{"name":"web_search"}</tool_call>')])
        search.assert_not_awaited(); self.assertTrue(any(e['type'] == 'warning' for e in events))

    async def test_unlisted_tool_and_extra_size_cannot_execute(self):
        malformed = native_call(1)
        malformed['function']['arguments'] = '{"query":"q","size":100}'
        events, search, payloads = await self.run_agent([calls_round([native_call(name='shell'), malformed]), answer_round('工具不可用')])
        search.assert_not_awaited()
        self.assertTrue(all(not json.loads(m['content'])['ok'] for m in payloads[1]['messages'] if m['role'] == 'tool'))

    async def test_search_failure_is_returned_to_model_not_fabricated(self):
        result = {'ok': False, 'error': '搜索超时', 'sources': []}
        events, _, payloads = await self.run_agent([calls_round([native_call()]), answer_round('搜索失败，无法核实。')], result)
        self.assertFalse(json.loads(payloads[1]['messages'][-1]['content'])['bilingual_results'][0]['ok'])
        self.assertEqual(next(e for e in events if e['type'] == 'citations')['sources'], [])

    async def test_repeated_query_reuses_result(self):
        events, search, _ = await self.run_agent([calls_round([native_call()]), calls_round([native_call()]), answer_round()])
        self.assertEqual(search.await_count, 2)
        self.assertTrue([e for e in events if e['type'] == 'tool_result'][-1]['cached'])

    async def test_loop_limit_finishes_with_tool_choice_none(self):
        rounds = [calls_round([native_call(query=f'q{i}')]) for i in range(4)] + [answer_round()]
        events, search, payloads = await self.run_agent(rounds)
        self.assertEqual(search.await_count, 8)
        self.assertEqual(payloads[-1]['tool_choice'], 'none')
        self.assertTrue(events[-1]['complete'])

    async def test_eight_search_limit_applies_to_multi_call_round(self):
        rounds = [calls_round([native_call(i, query=f'q{i}') for i in range(8)]), answer_round()]
        events, search, payloads = await self.run_agent(rounds)
        self.assertEqual(search.await_count, 8)
        self.assertEqual(payloads[-1]['tool_choice'], 'none')
        self.assertEqual(events[-1]['search_calls'], 8)

    async def test_cancellation_during_search_closes_request(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def waiting(*args):
            entered.set()
            try: await asyncio.Future()
            finally: cancelled.set()
        QueuedClient.responses = [calls_round([native_call()])]; QueuedClient.payloads = []
        body = server.ChatRequest(network_search=True, messages=[{'role': 'user', 'content': 'q'}])
        server.active['team'] = 1
        with patch.object(agent_stream.httpx, 'AsyncClient', QueuedClient), patch.object(agent_stream, 'search', waiting):
            generator = server.stream_reply(body, 'test-secret')
            while json.loads((await anext(generator))[6:])['type'] != 'tool_start': pass
            self.assertEqual(json.loads((await anext(generator))[6:])['type'], 'tool_start')
            pending = asyncio.create_task(anext(generator))
            await asyncio.wait_for(entered.wait(), timeout=1)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError): await pending
            await generator.aclose()
        self.assertTrue(cancelled.is_set()); self.assertEqual(server.active['team'], 0)


class SearchResultTests(unittest.TestCase):
    def test_size_is_not_a_model_controlled_parameter(self):
        for tool in st.TOOLS:
            self.assertNotIn('size', tool['function']['parameters']['properties']); self.assertEqual(tool['function']['parameters']['required'], ['query_zh','query_en'])
        self.assertEqual(st.RESULTS_PER_SEARCH, 10)

    def test_scope_mapping_and_unsafe_urls(self):
        raw = {'scholars': [{'title': 'unsafe', 'link': 'javascript:alert(1)'}, {'title': '论文', 'link': 'https://doi.org/abc', 'authors': ['张三']}]}
        result = st.normalize_results(raw, 'scholar')
        self.assertEqual(len(result), 1); self.assertEqual(result[0]['type'], 'scholar')
        with self.assertRaises(ValueError): st.normalize_results(raw, 'webpage')

    def test_bad_page_title_uses_hostname_without_inventing_title(self):
        url = 'https://www.mee.gov.cn/example.html'
        for title in ('var url="anything"; document.write("anything")', '您访问的链接即将离开生态环境部门户网站，是否继续？'):
            self.assertEqual(st.clean_title(title, url), '来源页面 · www.mee.gov.cn')

    def test_new_query_keeps_its_own_excerpt_for_an_existing_url(self):
        registry = []
        st.register_sources({'sources': [source()]}, registry)
        result = st.register_sources({'sources': [{**source(), 'snippet': '另一段相关摘要'}]}, registry)
        self.assertEqual(result['sources'][0]['snippet'], '另一段相关摘要')
        self.assertEqual(result['sources'][0]['source_id'], 'S1')

    def test_results_capped_at_ten_without_padding(self):
        raw = {'webpages': [{'link': f'https://example.org/{i}'} for i in range(20)]}
        self.assertEqual(len(st.normalize_results(raw, 'webpage')), 10)
        self.assertEqual(st.normalize_results({'webpages': []}, 'webpage'), [])

    def test_ids_deduplicated_across_tools(self):
        registry = []
        first = st.register_sources({'sources': [source()]}, registry)
        second = st.register_sources({'sources': [source(), source(2, 'scholar')]}, registry)
        self.assertEqual(first['sources'][0]['source_id'], second['sources'][0]['source_id'])
        self.assertEqual(len(registry), 2)

    def test_only_cited_results_are_listed_and_unknown_ids_flagged(self):
        registry = [{**source(i), 'source_id': f'S{i}'} for i in range(1, 4)]
        result = st.extract_citations('结论[S2]。补充[S2,S1]。缺失[S99]。`示例[S3]`', registry)
        self.assertEqual([s['source_id'] for s in result['sources']], ['S2', 'S1'])
        self.assertEqual(result['unmatched_ids'], ['S99'])

    def test_exact_source_links_match_but_foreign_links_do_not(self):
        registry = [{**source(), 'source_id': 'S1'}]
        result = st.extract_citations('[可靠](https://example.org/1) [未检索](https://another.test/x)', registry)
        self.assertEqual(len(result['sources']), 1)


if __name__ == '__main__': unittest.main()
