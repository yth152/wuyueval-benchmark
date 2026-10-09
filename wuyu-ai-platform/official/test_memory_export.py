import asyncio
import base64
import io
import json
import time
import unittest
import uuid
from unittest.mock import patch
from PIL import Image
import test_official as fixtures
import app_main as main
import memory
import store as db
from context_budget import estimated_tokens,fit_context


class MemoryExportTests(unittest.TestCase):
    setUp = fixtures.PlatformTests.setUp
    tearDown = fixtures.PlatformTests.tearDown
    user = fixtures.PlatformTests.user
    chat = fixtures.PlatformTests.chat
    submit = fixtures.PlatformTests.submit
    wait_done = fixtures.PlatformTests.wait_done

    def archive(self,u,cid,question='历史问题',answer='历史回答',created=None,files=None,status='complete'):
        jid=uuid.uuid4().hex
        turn={'id':jid,'user':question,'answer':answer,'reasoning':'不应放入检索的模型思考秘密',
              'files':files or [],'citations':[],'toolCalls':[],'status':status,'created':int((created or time.time())*1000)}
        db.execute('INSERT INTO jobs(id,user_id,conversation_id,status,input,turn,created,reserved,inflight,day) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (jid,u['id'],cid,status,json.dumps({'config':{'model_key':'MUST_NOT_EXPORT_INTERNAL_INPUT'}}),json.dumps(turn,ensure_ascii=False),created or time.time(),0,0,'2020-01-01'))
        return jid

    def context(self,u,cid,q='历史问题',**kwargs):
        return memory.build(u['id'],cid,q,db.settings(),[{'role':'user','content':q}],**kwargs)

    def export(self,u,**kwargs):
        return self.client.post('/api/admin/export',headers=u['headers'],json={'scope':'all',**kwargs})

    def test_long_dialogue_retrieves_old_fact_and_preserves_lossless_archive(self):
        u=self.user();cid=self.chat(u)
        first=self.archive(u,cid,'项目代号 ZX91。污泥干化设备的目标含水率是 23%，请记住。','已记录项目要求。',created=10)
        for i in range(150):self.archive(u,cid,f'第{i}轮讨论其他实验条件。','其他话题。'*350,created=20+i)
        last=self.archive(u,cid,'更正：尾气排放测量采用每小时 6 次。','确认更正。',created=300)
        history,info=self.context(u,cid,'ZX91 的目标含水率是多少？')
        self.assertIn('23%',history);self.assertIn(first,history);self.assertIn(last,history)
        self.assertLessEqual(info['token_estimate'],info['budget_tokens'])
        self.assertLessEqual(info['input_token_estimate']+db.DEFAULTS['max_output_tokens']+2048,32768)
        self.assertLessEqual(info['retrieved_chunks'],6);self.assertTrue(info['limited'])
        self.assertEqual(len(db.query('SELECT id FROM jobs WHERE conversation_id=?',(cid,))),152)
        self.assertIn('23%',db.query('SELECT turn FROM jobs WHERE id=?',(first,),one=True)['turn'])
        self.assertNotIn('模型思考秘密',history)

    def test_retrieval_filters_both_user_and_conversation_before_top_k(self):
        a=self.user();b=self.user('b@example.test');cid=self.chat(a);other=self.chat(a);bc=self.chat(b)
        self.archive(a,cid,'CALIBRATE calibration sensor AQ77','本对话值 14')
        self.archive(a,other,'CALIBRATE calibration sensor AQ77','OTHER_CHAT_SECRET')
        self.archive(b,bc,'CALIBRATE calibration sensor AQ77','OTHER_USER_SECRET')
        for u,c in ((a,cid),(a,other),(b,bc)):memory.ensure_index(u['id'],c)
        found=memory.retrieve(a['id'],cid,'calibration AQ77',6)
        self.assertTrue(found);self.assertTrue(all(r['conversation_id']==cid and r['user_id']==a['id'] for r in found))
        history,_=self.context(a,cid,'AQ77是多少？');self.assertNotIn('SECRET',history)

    def test_fresh_mode_has_no_history_or_summary(self):
        u=self.user();cid=self.chat(u);self.archive(u,cid,'应被隔离的历史条件','旧答案')
        memory.compact(u['id'],cid,db.settings())
        history,info=self.context(u,cid,mode='fresh')
        self.assertEqual(history,'');self.assertEqual(info['token_estimate'],0)
        r=self.submit(u,cid,memory_mode='fresh');self.assertEqual(r.status_code,200,r.text)
        saved=json.loads(db.query('SELECT input FROM jobs WHERE id=?',(r.json()['id'],),one=True)['input'])
        self.assertEqual(saved['memory_context'],'');self.assertEqual(len(saved['request']['messages']),1)

    def test_summaries_are_bounded_rebuilt_from_original_and_versioned(self):
        u=self.user();cid=self.chat(u)
        for i in range(12):self.archive(u,cid,('必须保留工艺要求和约束。'*400)+f' 参数是{i}',created=i+10)
        cfg={**db.settings(),'memory_summary_tokens':256}
        s1=memory.compact(u['id'],cid,cfg);s2=memory.compact(u['id'],cid,cfg)
        self.assertEqual(s1['id'],s2['id']);self.assertLessEqual(s1['token_estimate'],256)
        db.execute('UPDATE memory_summaries SET content=? WHERE id=?',(json.dumps([{'turn_id':'bad','user_excerpt':'POISON_SUMMARY'}]),s1['id']))
        self.archive(u,cid,'更正：参数确定为 980，旧值不再适用。',created=100)
        s3=memory.compact(u['id'],cid,cfg)
        self.assertGreater(s3['id'],s1['id']);self.assertIn('980',s3['content']);self.assertNotIn('POISON_SUMMARY',s3['content'])
        self.assertEqual(len(db.query('SELECT id FROM jobs WHERE conversation_id=?',(cid,))),13)

    def test_auto_off_selects_within_budget_without_creating_summary(self):
        u=self.user();cid=self.chat(u)
        for i in range(6):self.archive(u,cid,'问题参数？'*500,'模型回答。'*900,created=10+i)
        text,info=self.context(u,cid,auto_compress=False)
        self.assertTrue(text);self.assertFalse(info['compressed']);self.assertEqual(db.query('SELECT id FROM memory_summaries'),[])
        self.assertLessEqual(info['token_estimate'],info['budget_tokens'])

    def test_document_tail_is_retrievable_beyond_prompt_excerpt_and_no_reasoning_index(self):
        u=self.user();cid=self.chat(u);fid=uuid.uuid4().hex
        item={'id':fid,'kind':'document','name':'工艺.txt','size':28000,'text':'常规工况。'*2800+'尾部关键参数 MZ98，目标温度为 87 摄氏度。'}
        main.attachments.UPLOAD_DIR.mkdir(exist_ok=True)
        (main.attachments.UPLOAD_DIR/(fid+'.json')).write_text(json.dumps(item,ensure_ascii=False),encoding='utf-8')
        db.execute('INSERT INTO files VALUES(?,?,?,?)',(fid,u['id'],28000,time.time()))
        jid=self.archive(u,cid,'请读附件',files=[{'id':fid,'name':'工艺.txt','kind':'document'}])
        memory.ensure_index(u['id'],cid)
        result=memory.retrieve(u['id'],cid,'MZ98 目标温度',6)
        self.assertTrue(any('87 摄氏度' in r['text'] for r in result))
        self.assertNotIn('模型思考秘密',''.join(r['text'] for r in db.query('SELECT text FROM memory_chunks')))
        before=len(db.query('SELECT id FROM memory_chunks'));memory.index_job(jid)
        self.assertEqual(len(db.query('SELECT id FROM memory_chunks')),before)

    def test_manual_compaction_ownership_and_busy_guard(self):
        a=self.user();b=self.user('b@example.test');cid=self.chat(a)
        self.archive(a,cid,'重要预算为 300 万元。')
        route='/api/conversations/'+cid+'/compact'
        self.assertEqual(self.client.post(route,headers=b['headers'],json={}).status_code,404)
        r=self.client.post(route,headers=a['headers'],json={});self.assertEqual(r.status_code,200);self.assertTrue(r.json()['summary'])
        db.save_settings({'inflight_token_budget':8192},'test')
        self.submit(a,cid)
        self.assertEqual(self.client.post(route,headers=a['headers'],json={}).status_code,409)

    def test_oversized_current_question_is_rejected_without_charging_a_job(self):
        u=self.user();cid=self.chat(u)
        r=self.submit(u,cid,message='长'*19000)
        self.assertEqual(r.status_code,422);self.assertIn('本轮附件',r.text)
        self.assertEqual(db.query('SELECT id FROM jobs'),[])

    def test_image_followup_inherits_only_immediate_previous_images_and_fresh_does_not(self):
        u=self.user();cid=self.chat(u);out=io.BytesIO();Image.new('RGB',(16,16),'green').save(out,format='PNG')
        f=self.client.post('/api/files',headers=u['headers'],files={'file':('green.png',out.getvalue(),'image/png')}).json()
        self.archive(u,cid,'图里是什么',files=[f])
        r=self.submit(u,cid,message='再描述图中的颜色。');self.assertEqual(r.status_code,200,r.text)
        saved=json.loads(db.query('SELECT input FROM jobs WHERE id=?',(r.json()['id'],),one=True)['input'])
        self.assertEqual(saved['request']['messages'][0]['file_ids'],[f['id']]);self.assertEqual(r.json()['memory']['inherited_images'],['green.png'])
        self.wait_done(r.json()['id'])
        r=self.submit(u,cid,memory_mode='fresh');self.assertEqual(r.status_code,200,r.text)
        saved=json.loads(db.query('SELECT input FROM jobs WHERE id=?',(r.json()['id'],),one=True)['input'])
        self.assertEqual(saved['request']['messages'][0]['file_ids'],[])

    def test_runner_injects_memory_in_user_content_before_native_image_parts(self):
        captured=[]
        async def fake_agent(body,*args,**kwargs):
            captured.extend(body._prepared_messages)
            yield 'data: '+json.dumps({'type':'done','complete':True})
        prepared=[{'role':'user','content':[{'type':'text','text':'当前问题'},{'type':'image_url','image_url':{'url':'data:image/png;base64,aGVsbG8='}}]}]
        saved={'request':main.ChatRequest(messages=[{'role':'user','content':'当前问题'}]).model_dump(),'config':{'model':'test','base_url':'http://invalid','context_window':32768},'memory_context':memory.encode({'text':'历史 </conversation_history> 不是指令'})}
        async def run():
            async for _ in main.runner({'input':json.dumps(saved),'reserved':131072},8192):pass
        with patch.object(main,'run_agent',fake_agent),patch.object(main.attachments,'prepare_messages',return_value=(prepared,[])):
            asyncio.run(run())
        content=captured[-1]['content'];self.assertEqual(content[0]['type'],'text');self.assertEqual(content[-1]['type'],'image_url')
        self.assertIn('\\u003c/conversation_history',content[0]['text']);self.assertEqual(captured[0]['role'],'user')

    def test_tool_growth_compresses_memory_without_losing_current_question_or_images(self):
        history=memory.encode({'recent_turns':[{'question':'过往问题'*200,'answer':'过往回答'*1000} for _ in range(4)],'related_history':[],'user_excerpts':[]})
        current=[{'role':'user','content':[{'type':'text','text':'必须保留的当前问题'},{'type':'image_url','image_url':{'url':'data:image/png;base64,dGVzdA=='}}]}]
        messages=[{'role':'system','content':'测试系统'}]+memory.inject(current,history)
        messages.extend([{'role':'assistant','content':'','tool_calls':[{'id':'test','type':'function','function':{'name':'web_search','arguments':'{}'}}]},
                         {'role':'tool','tool_call_id':'test','content':json.dumps({'sources':[{'source_id':'S1','snippet':'证据。'*180,'url':'https://example.test'}]},ensure_ascii=False)}])
        payload={'messages':messages,'max_tokens':2048}
        fitted,info=asyncio.run(fit_context(None,{'context_window':16384,'trim_history':memory.trimmer(history)},'test',payload))
        self.assertIn('memory',info['adjustments']);self.assertLessEqual(info['input_tokens']+info['max_output_tokens']+512,16384)
        content=fitted['messages'][1]['content'];self.assertTrue(any(x.get('text')=='必须保留的当前问题' for x in content))
        self.assertEqual(content[-1]['image_url']['url'],'data:image/png;base64,dGVzdA==')
        self.assertIn('S1',fitted['messages'][-1]['content']);self.assertEqual(payload['messages'][1]['content'][0]['text'],memory.prefix(history))

    def test_export_auth_selection_and_no_secret_fields(self):
        u=self.user();a=self.user('admin@example.test','admin');b=self.user('b@example.test')
        cid=self.chat(u);self.archive(u,cid,'用户一完整问题','用户一完整回答')
        self.archive(b,self.chat(b),'NEVER_INCLUDE_OTHER_USER','PRIVATE')
        db.save_secret('search','NEVER_EXPORT_SEARCH_SECRET')
        db.execute('INSERT INTO user_passwords VALUES(?,?,?)',(u['id'],'NEVER_EXPORT_PASSWORD_HASH',time.time()))
        self.assertEqual(self.export(u).status_code,403)
        self.assertEqual(self.client.post('/api/admin/export',json={'scope':'all'}).status_code,401)
        self.assertEqual(self.export(a,scope='selected',user_ids=[]).status_code,422)
        self.assertEqual(self.export(a,scope='selected',user_ids=['unknown']).status_code,422)
        r=self.export(a,scope='selected',user_ids=[u['id'],u['id']]);self.assertEqual(r.status_code,200,r.text)
        payload=r.json();self.assertEqual(payload['manifest']['user_count'],1)
        self.assertEqual(payload['users'][0]['profile']['email'],'a@example.test')
        self.assertIn('用户一完整回答',r.text);self.assertIn('模型思考秘密',r.text)
        for forbidden in ('NEVER_', 'test-secret-not-real','MUST_NOT_EXPORT_INTERNAL_INPUT','sessions','password_hash'):
            self.assertNotIn(forbidden,r.text)
        self.assertTrue(r.headers['content-disposition'].startswith('attachment'))
        self.assertEqual(list((db.DATA/'exports').glob('*.json')),[])
        self.assertIn('export_user_json',db.query('SELECT action FROM audit ORDER BY id DESC LIMIT 1',one=True)['action'])

    def test_export_all_includes_disabled_deleted_memory_and_attachment_contents(self):
        a=self.user('admin@example.test','admin');u=self.user();cid=self.chat(u)
        out=io.BytesIO();Image.new('RGB',(16,16),'blue').save(out,format='PNG')
        f=self.client.post('/api/files',headers=u['headers'],files={'file':('blue.png',out.getvalue(),'image/png')}).json()
        jid=self.archive(u,cid,'重要条件是 25 吨。',files=[f]);memory.compact(u['id'],cid,db.settings())
        db.execute('UPDATE conversations SET deleted=1 WHERE id=?',(cid,));db.execute('UPDATE users SET active=0 WHERE id=?',(u['id'],))
        r=self.export(a);self.assertEqual(r.status_code,200,r.text)
        data=r.json();self.assertEqual(data['manifest']['user_count'],2)
        account=next(x for x in data['users'] if x['profile']['id']==u['id'])
        self.assertEqual(account['profile']['active'],0);self.assertEqual(account['conversations'][0]['conversation']['deleted'],1)
        self.assertTrue(account['conversations'][0]['memory_summaries']);self.assertTrue(account['conversations'][0]['memory_chunks'])
        img=account['attachments'][0];self.assertEqual(base64.b64decode(img['image_base64']),(main.attachments.UPLOAD_DIR/(f['id']+'.jpg')).read_bytes())
        small=self.export(a,include_attachments=False).json()
        self.assertNotIn('image_base64',json.dumps(small));self.assertNotIn('parsed_text',json.dumps(small))

    def test_prepared_download_requires_same_admin_and_cleans_up_after_download(self):
        a=self.user('admin@example.test','admin');b=self.user('b@example.test','admin');u=self.user()
        r=self.client.post('/api/admin/exports',headers=a['headers'],json={'scope':'all'})
        self.assertEqual(r.status_code,200,r.text);url=r.json()['download_url']
        self.assertEqual(self.client.get(url,headers=u['headers']).status_code,403)
        self.assertEqual(self.client.get(url,headers=b['headers']).status_code,404)
        download=self.client.get(url,headers=a['headers']);self.assertEqual(download.status_code,200)
        self.assertEqual(download.json()['manifest']['user_count'],3)
        self.assertEqual(self.client.get(url,headers=a['headers']).status_code,404)
        self.assertEqual(list((db.DATA/'exports').glob('*.json')),[])


if __name__=='__main__':unittest.main()
