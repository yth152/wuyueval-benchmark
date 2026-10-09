import asyncio
import io
import json
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import store as db
import auth
import app_main as main
from jobs import Scheduler, select_next


async def fake_runner(row, output):
    yield {'type':'content','text':'这是模拟回答。'}
    await asyncio.sleep(.12)
    yield {'type':'done','complete':True,'finish_reason':'stop','usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30},'seconds':.12}


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(db,'DATA',Path(self.temp.name));self.patch.start()
        self.scheduler_patch=patch.object(main,'scheduler',Scheduler(fake_runner));self.scheduler_patch.start()
        self.client=TestClient(main.app,base_url='http://127.0.0.1:8920');self.client.__enter__()
        db.save_secret('model','test-secret-not-real')
    def tearDown(self):
        self.client.__exit__(None,None,None);self.scheduler_patch.stop();self.patch.stop();self.temp.cleanup()
    def user(self,email='a@example.test',role='user'):
        uid=uuid.uuid4().hex
        db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,1,?)',(uid,email,'测试用户','测试机构','科研院所',role,time.time()))
        token=uuid.uuid4().hex
        db.execute('INSERT INTO sessions VALUES(?,?,?)',(db.digest(token),uid,time.time()+300))
        return {'id':uid,'headers':{'Cookie':auth.COOKIE+'='+token}}
    def chat(self,u):
        r=self.client.post('/api/conversations',json={},headers=u['headers']);self.assertEqual(r.status_code,200);return r.json()['id']
    def submit(self,u,cid,**kwargs):
        return self.client.post('/api/jobs',headers=u['headers'],json={'conversation_id':cid,'request_id':uuid.uuid4().hex,'message':'模拟问题',**kwargs})
    def wait_done(self,jid):
        for _ in range(100):
            row=db.query('SELECT * FROM jobs WHERE id=?',(jid,),one=True)
            if row and row['status'] not in ('queued','running'):return row
            time.sleep(.02)
        self.fail('job did not finish')
    def register(self,**extra):
        return self.client.post('/api/auth/register',json={'email':'new@example.test','password':'Test-password-123','name':'测试','organization':'研究机构','identity':'科研院所',**extra})
    def login(self,**extra):
        return self.client.post('/api/auth/login',json={'email':'new@example.test','password':'Test-password-123',**extra})

    def test_private_routes_require_login(self):
        for route in ['/api/me','/api/config','/api/conversations','/api/admin/settings','/api/admin/overview','/api/files/no/preview']:
            self.assertEqual(self.client.get(route).status_code,401,route)
        self.assertEqual(self.client.post('/api/jobs',json={'conversation_id':'x','request_id':'a'*16,'message':'x'}).status_code,401)

    def test_register_without_smtp_creates_only_user_with_hashed_credentials(self):
        self.assertEqual(self.register(role='admin').status_code,422)
        r=self.register();self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['user']['role'],'user')
        stored=db.query('SELECT password_hash FROM user_passwords',one=True)['password_hash']
        self.assertNotEqual(stored,'Test-password-123');self.assertTrue(auth.check_password('Test-password-123',stored))
        self.assertNotIn(stored,r.text);self.assertNotIn('password',r.text)
        self.assertIn('HttpOnly',r.headers['set-cookie']);self.assertIn('SameSite=strict',r.headers['set-cookie'])
        self.assertNotEqual(db.query('SELECT hash FROM sessions',one=True)['hash'],self.client.cookies.get(auth.COOKIE))
        self.assertNotIn('password',self.client.get('/api/me').text)
        self.assertEqual(self.client.get('/api/admin/settings').status_code,403)

    def test_login_needs_correct_password_and_no_profile_fields(self):
        self.register();self.client.post('/api/auth/logout',json={})
        wrong=self.login(password='wrong-password')
        unknown=self.login(email='nobody@example.test')
        self.assertEqual(wrong.status_code,401);self.assertEqual(unknown.json(),wrong.json())
        self.assertEqual(self.login(email='NEW@EXAMPLE.TEST').status_code,200)
        self.assertEqual(self.client.get('/api/me').json()['organization'],'研究机构')

    def test_duplicate_registration_is_atomic_and_cannot_take_over_existing_user(self):
        def attempt(_):
            try:return bool(auth.register('race@example.test','Test-password-123','姓名','机构','科研院所','127.0.0.1'))
            except Exception:return False
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(results),1)
        self.assertEqual(self.register(email='RACE@EXAMPLE.TEST',password='Different-pass123').status_code,409)
        self.assertEqual(self.login(email='race@example.test',password='Different-pass123').status_code,401)
        self.assertEqual(self.login(email='race@example.test').status_code,200)

    def test_invalid_registration_profile_and_password(self):
        for extra in ({'password':'short'},{'password':' '*8},{'name':' '},{'organization':' '},{'identity':'invalid'},{'email':'bad-email'}):
            self.assertEqual(self.register(**extra).status_code,422)
        self.assertEqual(len(db.query('SELECT * FROM users')),0)

    def test_registration_closed_does_not_block_existing_login(self):
        self.register();self.client.post('/api/auth/logout',json={})
        db.save_settings({'registration_open':False},'test')
        self.assertEqual(self.register(email='other@example.test').status_code,403)
        self.assertEqual(self.login().status_code,200)

    def test_failed_password_attempts_persist_and_are_rate_limited(self):
        self.register();self.client.post('/api/auth/logout',json={})
        for _ in range(10):self.assertEqual(self.login(password='wrong-password').status_code,401)
        self.assertEqual(self.login().status_code,429)
        db.execute('UPDATE password_attempts SET created=?',(time.time()-601,))
        self.assertEqual(self.login().status_code,200)

    def test_removed_otp_and_bootstrap_endpoints_are_not_available(self):
        for route in ('code','setup'):
            self.assertEqual(self.client.post('/api/auth/'+route,json={}).status_code,404)
        public=self.client.get('/api/public').json()
        self.assertEqual(public['auth_method'],'email_password')
        self.assertNotIn('mail_ready',public);self.assertNotIn('setup_required',public)

    def test_operator_admin_and_password_recovery_preserve_identity(self):
        uid=auth.create_admin('admin@example.test','Admin-password-123','管理员','机构')
        self.assertEqual(self.login(email='admin@example.test',password='Admin-password-123').status_code,200)
        self.assertEqual(self.client.get('/api/admin/settings').status_code,200)
        cid=self.client.post('/api/conversations',json={}).json()['id']
        auth.set_password('admin@example.test','Replacement-pass123')
        self.assertEqual(self.client.get('/api/me').status_code,401)
        self.assertEqual(self.login(email='admin@example.test',password='Admin-password-123').status_code,401)
        self.assertEqual(self.login(email='admin@example.test',password='Replacement-pass123').json()['user']['id'],uid)
        self.assertEqual(self.client.get('/api/conversations/'+cid).status_code,200)
        self.assertNotIn('password_hash',self.client.get('/api/admin/overview').text)

    def test_migrated_passwordless_accounts_cannot_be_claimed_by_registration(self):
        old=self.user('legacy@example.test','admin')
        db.init()
        self.assertEqual(self.register(email='legacy@example.test').status_code,409)
        self.assertEqual(self.login(email='legacy@example.test').status_code,401)
        auth.set_password('legacy@example.test','Test-password-123')
        self.assertEqual(self.login(email='legacy@example.test').json()['user']['id'],old['id'])

    def test_password_hashes_use_different_salts(self):
        first=auth.hash_password('Test-password-123');second=auth.hash_password('Test-password-123')
        self.assertNotEqual(first,second);self.assertFalse(auth.check_password('wrong-password',first))

    def test_admin_boundary_encrypted_keys_and_preservation(self):
        u=self.user();a=self.user('admin@example.test','admin')
        self.assertEqual(self.client.get('/api/admin/settings',headers=u['headers']).status_code,403)
        body=dict(db.DEFAULTS,model_key='new-private-key',search_key='search-private-key')
        r=self.client.put('/api/admin/settings',json=body,headers=a['headers']);self.assertEqual(r.status_code,200,r.text)
        result=self.client.get('/api/admin/settings',headers=a['headers']).text
        self.assertNotIn('private-key',result);self.assertNotIn('new-private-key',(db.DATA/'platform.sqlite3').read_bytes().decode('latin1'))
        body.update(model_key='',search_key='');self.client.put('/api/admin/settings',json=body,headers=a['headers'])
        self.assertEqual(db.secret('model'),'new-private-key')

    def test_invalid_resource_configuration_is_rejected(self):
        a=self.user('admin@example.test','admin');body=dict(db.DEFAULTS)
        for field,value in [('inflight_token_budget',8192),('per_user_parallel',3),('busy_output_tokens',16384),('base_url','file:///etc/passwd')]:
            with self.subTest(field=field):self.assertEqual(self.client.put('/api/admin/settings',headers=a['headers'],json={**body,field:value}).status_code,422)

    def test_user_cannot_override_server_generation_settings(self):
        u=self.user();cid=self.chat(u)
        self.assertEqual(self.submit(u,cid,system='override',max_tokens=100000).status_code,422)

    def test_owned_conversations_and_exports(self):
        a=self.user();b=self.user('b@example.test');cid=self.chat(a)
        for path in ['/api/conversations/'+cid,'/api/conversations/'+cid+'/export']:
            self.assertEqual(self.client.get(path,headers=b['headers']).status_code,404)
        self.assertEqual(self.submit(b,cid).status_code,404)

    def test_attachments_isolated_even_if_id_is_known(self):
        a=self.user();b=self.user('b@example.test');out=io.BytesIO();Image.new('RGB',(16,16),'green').save(out,format='PNG')
        upload=self.client.post('/api/files',headers=a['headers'],files={'file':('image.png',out.getvalue(),'image/png')});self.assertEqual(upload.status_code,200)
        fid=upload.json()['id'];self.assertEqual(self.client.get('/api/files/'+fid+'/preview',headers=b['headers']).status_code,404)
        self.assertEqual(self.submit(b,self.chat(b),file_ids=[fid]).status_code,404)

    def test_jobs_survive_logout_and_do_not_need_stream_connection(self):
        a=self.user();cid=self.chat(a);r=self.submit(a,cid);self.assertEqual(r.status_code,200)
        self.client.post('/api/auth/logout',json={},headers=a['headers'])
        row=self.wait_done(r.json()['id']);self.assertEqual(row['status'],'complete');self.assertEqual(row['charged'],30)
        self.assertEqual(self.client.get('/api/conversations/'+cid,headers=a['headers']).status_code,401)
        token=uuid.uuid4().hex;db.execute('INSERT INTO sessions VALUES(?,?,?)',(db.digest(token),a['id'],time.time()+300))
        self.assertIn('模拟回答',self.client.get('/api/conversations/'+cid,headers={'Cookie':auth.COOKIE+'='+token}).text)

    def test_idempotency_and_same_chat_serialization(self):
        u=self.user();cid=self.chat(u);request_id=uuid.uuid4().hex
        first=self.submit(u,cid,request_id=request_id);second=self.submit(u,cid,request_id=request_id)
        self.assertEqual(first.json()['id'],second.json()['id']);self.assertEqual(len(db.query('SELECT id FROM jobs')),1)
        self.assertEqual(self.submit(u,cid).status_code,409)

    def test_daily_reservation_prevents_oversubscription_and_delete_keeps_usage(self):
        u=self.user();db.save_settings({'daily_tokens':131072,'daily_questions':1},'test');cid=self.chat(u)
        first=self.submit(u,cid);self.assertEqual(first.status_code,200)
        self.assertEqual(self.submit(u,self.chat(u)).status_code,429)
        self.wait_done(first.json()['id']);self.assertEqual(self.client.request('DELETE','/api/conversations/'+cid,json={},headers=u['headers']).status_code,200)
        self.assertNotIn(cid,[c['id'] for c in self.client.get('/api/conversations',headers=u['headers']).json()])
        self.assertEqual(self.submit(u,self.chat(u)).status_code,429)

    def test_queue_limit_cancel_releases_reservation(self):
        u=self.user();db.save_settings({'per_user_queue':1,'daily_tokens':131072},'test')
        # Leave no capacity so this particular task stays queued.
        db.save_settings({'inflight_token_budget':8192},'test')
        first=self.submit(u,self.chat(u));self.assertEqual(first.status_code,200)
        self.assertEqual(self.submit(u,self.chat(u)).status_code,429)
        jid=first.json()['id'];self.assertEqual(self.client.post('/api/jobs/'+jid+'/cancel',json={},headers=u['headers']).status_code,200)
        self.assertEqual(db.query('SELECT charged FROM jobs WHERE id=?',(jid,),one=True)['charged'],0)
        self.assertEqual(self.submit(u,self.chat(u)).status_code,200)

    def test_disabling_user_revokes_session_and_cancels_jobs(self):
        u=self.user();a=self.user('admin@example.test','admin');db.save_settings({'inflight_token_budget':8192},'test')
        task=self.submit(u,self.chat(u)).json()
        r=self.client.patch('/api/admin/users/'+u['id'],json={'active':False},headers=a['headers']);self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/me',headers=u['headers']).status_code,401)
        self.assertEqual(db.query('SELECT status FROM jobs WHERE id=?',(task['id'],),one=True)['status'],'stopped')

    def test_host_origin_and_json_guards(self):
        self.assertEqual(self.client.get('/api/public',headers={'Host':'evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/auth/register',json={'email':'a@example.test'},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/auth/register',content='email=a').status_code,415)

    def test_static_compatibility_assets_do_not_return_server_errors(self):
        # Cached pre-build HTML can still request these legacy paths.  They
        # must resolve to a safe local asset instead of a missing-file 500.
        for path in ('/logo.png', '/favicon.svg'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertTrue(response.headers['content-type'].startswith('image/'))

    def test_running_task_can_be_cancelled_without_blocking_next_user(self):
        async def slow(row, output):
            yield {'type':'content','text':'已收到的内容'}
            await asyncio.sleep(10)
        main.scheduler.runner=slow
        u=self.user();jid=self.submit(u,self.chat(u)).json()['id']
        for _ in range(100):
            row=db.query('SELECT * FROM jobs WHERE id=?',(jid,),one=True)
            if '已收到' in row['turn']:break
            time.sleep(.02)
        self.assertEqual(row['status'],'running')
        self.assertEqual(self.client.post('/api/jobs/'+jid+'/cancel',json={},headers=u['headers']).status_code,200)
        stopped=self.wait_done(jid)
        self.assertEqual(stopped['status'],'stopped');self.assertIn('已收到',stopped['turn'])
        self.assertEqual(stopped['charged'],stopped['reserved'])
        main.scheduler.runner=fake_runner
        other=self.user('next@example.test')
        self.assertEqual(self.wait_done(self.submit(other,self.chat(other)).json()['id'])['status'],'complete')

    def test_actual_dispatch_rotates_users_and_applies_busy_budget(self):
        starts=[]
        async def recorded(row,output):
            starts.append((row['user_id'],output))
            async for event in fake_runner(row,output):yield event
        main.scheduler.runner=recorded
        db.save_settings({'inflight_token_budget':8192,'max_parallel':1},'test')
        a=self.user();b=self.user('b@example.test')
        ids=[self.submit(a,self.chat(a)).json()['id'] for _ in range(2)]
        ids.append(self.submit(b,self.chat(b)).json()['id'])
        db.save_settings({'inflight_token_budget':32768},'test')
        for jid in ids:self.wait_done(jid)
        self.assertEqual([u for u,_ in starts],[a['id'],b['id'],a['id']])
        self.assertEqual(starts[0][1],db.DEFAULTS['busy_output_tokens'])

    def test_restart_retains_interrupted_output_and_resumes_queued_task(self):
        db.save_settings({'inflight_token_budget':8192},'test')
        u=self.user();old=self.submit(u,self.chat(u)).json()['id'];queued=self.submit(u,self.chat(u)).json()['id']
        self.client.__exit__(None,None,None)
        row=db.query('SELECT * FROM jobs WHERE id=?',(old,),one=True);turn=json.loads(row['turn']);turn['answer']='重启前内容'
        db.execute("UPDATE jobs SET status='running',turn=? WHERE id=?",(json.dumps(turn,ensure_ascii=False),old))
        main.scheduler=Scheduler(fake_runner)
        db.save_settings({'inflight_token_budget':65536},'test')
        self.client=TestClient(main.app,base_url='http://127.0.0.1:8920');self.client.__enter__()
        recovered=db.query('SELECT * FROM jobs WHERE id=?',(old,),one=True)
        self.assertEqual(recovered['status'],'interrupted');self.assertIn('重启前内容',recovered['turn'])
        self.assertEqual(self.wait_done(queued)['status'],'complete')


class FairnessTests(unittest.TestCase):
    def job(self,id,user,size=32768):return {'id':id,'user_id':user,'inflight':size}
    def test_round_robin_does_not_let_one_user_fill_every_slot(self):
        rows=[self.job('a1','A'),self.job('a2','A'),self.job('a3','A'),self.job('b1','B'),self.job('c1','C')]
        first=select_next(rows,[],db.DEFAULTS,None);self.assertEqual(first['id'],'a1')
        second=select_next(rows[1:],[first],db.DEFAULTS,'A');self.assertEqual(second['id'],'b1')
        third=select_next([r for r in rows if r['id'] not in ('a1','b1')],[],db.DEFAULTS,'B');self.assertEqual(third['id'],'a2')
        # When B still has work, rotate past B to C, not back to A.
        self.assertEqual(select_next(rows,[],db.DEFAULTS,'B')['id'],'c1')
    def test_capacity_and_per_user_limits_are_both_enforced(self):
        running=[self.job('a1','A')]
        self.assertIsNone(select_next([self.job('a2','A')],running,db.DEFAULTS,'A'))
        self.assertIsNone(select_next([self.job('b1','B',40000)],running,db.DEFAULTS,'A'))
        self.assertIsNone(select_next([self.job('c1','C')],running+[self.job('b1','B')],db.DEFAULTS,'B'))


if __name__=='__main__':unittest.main()
