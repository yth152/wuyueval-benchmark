"""Native model request budgeting; all transports are deterministic mocks."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
sys.path.append(str(Path(__file__).resolve().parent.parent/'model-chat'))
import agent_stream
from server import ChatRequest
from test_agent import QueuedClient, answer_round, calls_round, native_call


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, responses, limit, network=False):
        QueuedClient.responses=copy.deepcopy(responses);QueuedClient.payloads=[]
        body=ChatRequest(messages=[{'role':'user','content':'模拟预算检查'}],network_search=network)
        search=AsyncMock(return_value={'ok':True,'sources':[]})
        async def fit(client,provider,key,payload,**kwargs):
            return payload,{'input_tokens':1000,'adjustments':[],'max_output_tokens':8192}
        with patch.object(agent_stream.httpx,'AsyncClient',QueuedClient),patch.object(agent_stream,'fit_context',fit),patch.object(agent_stream,'search',search):
            events=[json.loads(raw[6:]) async for raw in agent_stream.run_agent(body,'mock',provider_config={'model':'wire-alias','base_url':'http://model.example.test/v1','context_window':32768},search_key='mock-search',total_token_budget=limit)]
        return events,QueuedClient.payloads,search

    async def test_clamps_output_and_marks_missing_usage_as_estimate(self):
        events,payloads,_=await self.run_fake([answer_round('预算回答')],1800)
        self.assertEqual(payloads[0]['max_tokens'],800);self.assertEqual(payloads[0]['model'],'wire-alias')
        self.assertTrue(events[-1]['usage_estimated']);self.assertEqual(events[-1]['usage']['total_tokens'],1800)

    async def test_insufficient_input_plus_output_never_calls_upstream(self):
        events,payloads,_=await self.run_fake([],1400)
        self.assertEqual(payloads,[]);self.assertEqual(events[-1]['error_kind'],'task_budget')

    async def test_missing_usage_cannot_reset_budget_between_tool_rounds(self):
        events,payloads,search=await self.run_fake([calls_round([native_call()])],10000,True)
        self.assertEqual(len(payloads),1);self.assertEqual(events[-1]['error_kind'],'task_budget')
        self.assertEqual(search.await_count,2)
        self.assertEqual(search.await_args_list[0].kwargs['api_key'],'mock-search')

if __name__=='__main__':unittest.main()
