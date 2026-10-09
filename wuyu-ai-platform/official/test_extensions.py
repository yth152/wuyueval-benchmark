"""Extension boundaries and native Agent integration, using disposable data only."""
import asyncio
import copy
import io
import json
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from PIL import Image
from starlette.websockets import WebSocketDisconnect
import app_main as main
import agent_stream
import store as db
import tool_registry as tools
import voice
from server import ChatRequest
from test_agent import QueuedClient, answer_round, calls_round
import test_official as fixtures


class ExtensionTests(unittest.TestCase):
    setUp = fixtures.PlatformTests.setUp
    tearDown = fixtures.PlatformTests.tearDown
    user = fixtures.PlatformTests.user
    chat = fixtures.PlatformTests.chat
    submit = fixtures.PlatformTests.submit
    wait_done = fixtures.PlatformTests.wait_done

    def configure_tool(self, **kwargs):
        self.admin = self.user('admin@example.test', 'admin')
        item = tools.ToolConfig(id='industry_query', label='行业数据查询', description='查询固废行业数据',
                                endpoint='https://example.test/query', **kwargs).model_dump(exclude={'api_key'})
        payload = tools.catalog(); payload['tools'].append(item)
        response = self.client.put('/api/admin/tools', json=payload, headers=self.admin['headers'])
        self.assertEqual(response.status_code,200,response.text)
        return tools.catalog()['tools'][-1]

    def execute(self, item, arguments='{"query":"固废"}', runtime=None):
        runtime = runtime or tools.ToolRuntime([item])
        async def run():
            return [e async for e in runtime.execute({'id':'c1','function':{'name':item['id'],'arguments':arguments}},[],0)]
        return asyncio.run(run())

    def test_tool_crud_permissions_revision_and_encrypted_key(self):
        item = self.configure_tool(auth_type='bearer')
        normal = self.user()
        self.assertEqual(self.client.get('/api/admin/tools',headers=normal['headers']).status_code,403)
        data = tools.catalog();data['tools'][-1]['api_key']='private-tool-key'
        response = self.client.put('/api/admin/tools',json=data,headers=self.admin['headers'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(db.secret('tool_industry_query'),'private-tool-key')
        stored=db.query('SELECT value FROM settings WHERE key=?',('tool_industry_query_secret',),one=True)
        self.assertNotIn('private-tool-key', stored['value'])
        self.assertNotIn('private-tool-key', self.client.get('/api/admin/tools',headers=self.admin['headers']).text)
        self.assertEqual(self.client.put('/api/admin/tools',json=data,headers=self.admin['headers']).status_code,409)
        data=tools.catalog();self.client.put('/api/admin/tools',json=data,headers=self.admin['headers'])
        self.assertEqual(db.secret('tool_industry_query'),'private-tool-key')
        public=self.client.get('/api/config',headers=normal['headers'])
        self.assertTrue(public.json()['search_ready']);self.assertNotIn('example.test',public.text)

    def test_dynamic_native_call_sources_and_no_thinking_parameter(self):
        item=self.configure_tool()
        value={'ok':True,'data':'{"amount":123}','sources':[{'title':'来源','url':'https://example.test/article','type':'webpage','snippet':'统计量123'}]}
        native={'id':'call_custom','type':'function','function':{'name':'industry_query','arguments':'{"query":"资源化"}'}}
        async def run():
            QueuedClient.responses=copy.deepcopy([calls_round([native]),answer_round('行业数据为123。[S1]')]);QueuedClient.payloads=[]
            body=ChatRequest(messages=[{'role':'user','content':'查询行业数据'}],network_search=True)
            with patch.object(agent_stream.httpx,'AsyncClient',QueuedClient),patch.object(tools,'http_call',AsyncMock(return_value=value)) as execute:
                events=[json.loads(raw[6:]) async for raw in agent_stream.run_agent(body,'mock',provider_config={'model':'new-vl','base_url':'http://model.test/v1','context_window':32768,'thinking_mode':'none','display_name':'新模型'},tool_runtime=tools.ToolRuntime([item]))]
                self.assertEqual(execute.await_count,1)
            self.assertEqual(events[-1]['type'],'done',events[-1]);self.assertTrue(events[-1]['complete'])
            self.assertEqual(next(e for e in events if e['type']=='citations')['sources'][0]['source_id'],'S1')
            first,second=QueuedClient.payloads
            self.assertEqual(first['tools'][0]['function']['name'],'industry_query')
            self.assertNotIn('chat_template_kwargs',first);self.assertNotIn('enable_thinking',first)
            self.assertTrue(any(m['role']=='tool' and '123' in m['content'] for m in second['messages']))
        asyncio.run(run())

    def test_invalid_arguments_budget_and_admin_disable_do_not_dispatch(self):
        item=self.configure_tool()
        with patch.object(tools,'http_call',AsyncMock()) as call:
            events=self.execute(item,'{"wrong":"bad"}')
            self.assertFalse(events[1]['ok']);call.assert_not_awaited()
            events=self.execute(item,runtime=tools.ToolRuntime([item],max_calls=0))
            self.assertFalse(events[1]['ok']);call.assert_not_awaited()
            data=tools.catalog();data['tools'][-1]['enabled']=False
            self.client.put('/api/admin/tools',json=data,headers=self.admin['headers'])
            events=self.execute(item);self.assertFalse(events[1]['ok']);call.assert_not_awaited()

    def test_http_mapping_sources_secret_redaction_and_no_redirect(self):
        item=self.configure_tool(auth_type='bearer',argument_mapping={'query':'keyword'},static_arguments={'size':10},sources_path='data.articles',source_fields={'title':'name','url':'link','snippet':'abstract'})
        db.save_secret('tool_industry_query','masked-key')
        import httpx
        original=httpx.AsyncClient;requests=[]
        def handle(request):
            requests.append(request)
            return httpx.Response(200,json={'data':{'articles':[{'name':'文章','link':'https://example.test/a','abstract':'结果'}, {'name':'bad','link':'javascript:alert(1)'}]},'token':'masked-key'})
        def client(**kwargs): return original(transport=httpx.MockTransport(handle),**kwargs)
        with patch.object(tools,'check_address',AsyncMock()),patch.object(tools.httpx,'AsyncClient',client):
            value=asyncio.run(tools.http_call(item,{'query':'固废'}))
        self.assertEqual(json.loads(requests[0].content),{'keyword':'固废','size':10})
        self.assertEqual(requests[0].headers['Authorization'],'Bearer masked-key')
        self.assertNotIn('masked-key',json.dumps(value));self.assertEqual(len(value['sources']),1)
        self.assertEqual(value['sources'][0]['title'],'文章')

    def test_private_address_denied_and_schema_ref_rejected(self):
        item=self.configure_tool()
        address=[(2,1,6,'',('169.254.169.254',80))]
        with patch.object(tools.socket,'getaddrinfo',return_value=address):
            with self.assertRaises(ValueError): asyncio.run(tools.check_address(item))
            with self.assertRaises(ValueError): asyncio.run(tools.check_address(item|{'allow_private':True}))
        data=tools.catalog();data['tools'][-1]['parameters']={'type':'object','$ref':'https://example.test/schema'}
        self.assertEqual(self.client.put('/api/admin/tools',json=data,headers=self.admin['headers']).status_code,422)

    def test_model_capabilities_and_independent_settings(self):
        admin=self.user(role='admin');payload=dict(db.DEFAULTS)
        profile={'id':'custom','display_name':'自定义模型','model':'vision-model','base_url':'https://example.test/v1','context_window':16384,
                 'max_output_tokens':2048,'temperature':0.5,'thinking_mode':'none','supports_vision':False,'supports_tools':False,'api_key':'model-private'}
        payload['additional_models']=[profile]
        response=self.client.put('/api/admin/settings',json=payload,headers=admin['headers']);self.assertEqual(response.status_code,200,response.text)
        user=self.user('normal@example.test');cid=self.chat(user)
        result=self.submit(user,cid,model_id='custom');self.assertEqual(result.status_code,200,result.text)
        row=self.wait_done(result.json()['id']);saved=json.loads(row['input'])
        self.assertEqual(saved['request']['max_tokens'],2048);self.assertFalse(saved['request']['thinking'])
        self.assertEqual(saved['request']['temperature'],0.5)
        self.assertEqual(self.submit(user,cid,model_id='custom',network_search=True).status_code,422)
        raw=io.BytesIO();Image.new('RGB',(32,32),'green').save(raw,format='PNG')
        upload=self.client.post('/api/files',files={'file':('image.png',raw.getvalue(),'image/png')},headers=user['headers'])
        self.assertEqual(upload.status_code,200,upload.text)
        self.assertEqual(self.submit(user,cid,model_id='custom',file_ids=[upload.json()['id']]).status_code,422)

    def voice_setup(self):
        admin=self.user('voice-admin@example.test','admin')
        result=self.client.put('/api/admin/voice',headers=admin['headers'],json={'enabled':True,'app_id':'fixture-id','api_key':'fixture-key','api_secret':'fixture-secret','max_seconds':10})
        self.assertEqual(result.status_code,200,result.text)
        return admin

    def test_voice_permissions_config_and_websocket_origin(self):
        admin=self.voice_setup();user=self.user()
        data=self.client.get('/api/config',headers=user['headers']);self.assertTrue(data.json()['voice']['ready'])
        self.assertNotIn('fixture',data.text)
        self.assertEqual(self.client.get('/api/admin/voice',headers=user['headers']).status_code,403)
        self.assertEqual(self.client.put('/api/admin/voice',headers=admin['headers'],json={'enabled':True}).status_code,200)
        self.assertEqual(db.secret('voice_api_secret'),'fixture-secret')
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('ws://127.0.0.1:8920/api/voice/stream',headers=user['headers']|{'Origin':'https://evil.example'}):pass
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('ws://127.0.0.1:8920/api/voice/stream',headers={'Origin':'http://127.0.0.1:8920'}):pass

    def test_voice_pcm_to_final_and_cleanup(self):
        self.voice_setup();user=self.user();received=[]
        class Upstream:
            def __init__(self):self.ended=asyncio.Event()
            async def send(self,value):
                received.append(value)
                if isinstance(value,str):self.ended.set()
            async def recv(self):
                await self.ended.wait();return 'final'
        class Segments:
            final=False
            def update(self,raw):self.final=True;return True
            def text(self):return '请分析固废资源化技术。'
        @asynccontextmanager
        async def session(credentials):
            self.assertEqual(credentials['api_key'],'fixture-key')
            yield Upstream(),'session-fixture'
        with patch.object(voice.xfyun_asr,'open_session',session),patch.object(voice.xfyun_asr,'Segments',Segments):
            with self.client.websocket_connect('ws://127.0.0.1:8920/api/voice/stream',headers=user['headers']|{'Origin':'http://127.0.0.1:8920'}) as ws:
                self.assertEqual(ws.receive_json()['type'],'ready');ws.send_bytes(b'\x00'*1280);ws.send_json({'type':'end'})
                result=ws.receive_json();self.assertEqual(result['type'],'final');self.assertIn('资源化',result['text'])
        self.assertNotIn(user['id'],voice.active)
        call=db.query('SELECT * FROM voice_calls',one=True);self.assertEqual(call['status'],'complete');self.assertEqual(call['seconds'],.04)
        self.assertEqual(len(received[0]),1280)

    def test_voice_bad_frame_releases_slot(self):
        self.voice_setup();user=self.user()
        class Upstream:
            async def recv(self):await asyncio.sleep(60)
        @asynccontextmanager
        async def session(credentials):yield Upstream(),'session'
        with patch.object(voice.xfyun_asr,'open_session',session):
            with self.client.websocket_connect('ws://127.0.0.1:8920/api/voice/stream',headers=user['headers']|{'Origin':'http://127.0.0.1:8920'}) as ws:
                self.assertEqual(ws.receive_json()['type'],'ready');ws.send_bytes(b'123')
                self.assertEqual(ws.receive_json()['type'],'error')
        self.assertNotIn(user['id'],voice.active)

    def test_custom_data_compacts_without_losing_citations(self):
        from context_budget import fit_context, estimated_tokens
        messages=[{'role':'user','content':'比较这些资料'}]
        for i in range(8):
            messages.append({'role':'tool','tool_call_id':str(i),'content':json.dumps({'ok':True,'data':'资料'*3000,'sources':[{'source_id':f'S{i+1}','url':f'https://example.test/{i}','title':'资料'}]},ensure_ascii=False)})
        payload={'messages':messages,'max_tokens':2048}
        result,budget=asyncio.run(fit_context(None,{'context_window':16384},'mock',payload))
        self.assertIn('evidence',budget['adjustments'])
        self.assertLessEqual(estimated_tokens(result)+result['max_tokens']+512,16384)
        for i,message in enumerate(result['messages'][1:]):
            value=json.loads(message['content']);self.assertTrue(value['data_truncated_for_context'])
            self.assertEqual(value['sources'][0]['source_id'],f'S{i+1}')
        self.assertEqual(len(json.loads(payload['messages'][1]['content'])['data']),6000)


if __name__ == '__main__': unittest.main()
