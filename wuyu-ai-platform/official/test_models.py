import asyncio
import json
import unittest
from unittest.mock import patch

import app_main as main
import store as db
import test_official as fixtures

PROFILE = {'id':'baseline','display_name':'在线 Qwen 27B','model':'qwen3.8-27b',
           'base_url':'https://example.test/v1','context_window':24576,'protocol':'baseline','enabled':True}


class ModelTests(unittest.TestCase):
    setUp = fixtures.PlatformTests.setUp
    tearDown = fixtures.PlatformTests.tearDown
    user = fixtures.PlatformTests.user
    chat = fixtures.PlatformTests.chat
    submit = fixtures.PlatformTests.submit
    wait_done = fixtures.PlatformTests.wait_done

    def configure(self):
        db.save_settings({'additional_models':[PROFILE]},'test')
        db.save_secret('provider_baseline','online-fixture-secret')

    def test_public_catalog_no_credentials_or_endpoints(self):
        self.configure();u=self.user()
        response=self.client.get('/api/config',headers=u['headers'])
        self.assertEqual([p['id'] for p in response.json()['models']],['team','baseline'])
        for value in ('online-fixture-secret','example.test','base_url','api_key'): self.assertNotIn(value,response.text)
        self.assertTrue(all(p['ready'] for p in response.json()['models']))

    def test_unknown_disabled_or_unconfigured_does_not_charge(self):
        u=self.user();cid=self.chat(u)
        self.assertEqual(self.submit(u,cid,model_id='missing').status_code,422)
        db.save_settings({'additional_models':[PROFILE]},'test')
        self.assertEqual(self.submit(u,cid,model_id='baseline').status_code,503)
        self.configure();db.save_settings({'additional_models':[PROFILE|{'enabled':False}]},'test')
        self.assertEqual(self.submit(u,cid,model_id='baseline').status_code,422)
        self.assertEqual(db.query('SELECT COUNT(*) AS n FROM jobs',one=True)['n'],0)

    def test_routing_snapshot_context_and_secret_isolation(self):
        self.configure();u=self.user();cid=self.chat(u)
        response=self.submit(u,cid,model_id='baseline');self.assertEqual(response.status_code,200,response.text)
        row=self.wait_done(response.json()['id']);saved=json.loads(row['input'])
        self.assertEqual(row['inflight'],24576)
        self.assertEqual(saved['request']['provider'],'baseline')
        self.assertEqual(saved['config']['model_id'],'baseline')
        self.assertEqual(response.json()['modelLabel'],'在线 Qwen 27B')
        self.assertEqual(response.json()['request']['model_id'],'baseline')
        self.assertNotIn('online-fixture-secret',row['input'])
        db.save_settings({'additional_models':[]},'test')
        captured=[]
        async def fake(body,key,**kwargs):
            captured.append((body,key,kwargs))
            yield 'data: '+json.dumps({'type':'content','text':'ok'})
        async def run():
            with patch.object(main,'run_agent',fake): return [item async for item in main.runner(row,4096)]
        asyncio.run(run())
        body,key,args=captured[0]
        self.assertEqual(key,'online-fixture-secret')
        self.assertEqual(body.provider,'baseline')
        self.assertEqual(body.max_tokens,4096)
        self.assertEqual(args['provider_config']['base_url'],PROFILE['base_url'])
        self.assertEqual(args['provider_config']['model'],PROFILE['model'])
        # Legacy jobs remain on the original endpoint and key.
        saved['config'].pop('model_id');saved['request']['provider']='team';row['input']=json.dumps(saved)
        asyncio.run(run());self.assertEqual(captured[-1][1],'test-secret-not-real')

    def test_admin_save_encrypts_and_blank_preserves(self):
        admin=self.user(role='admin');payload=dict(db.DEFAULTS)
        payload['additional_models']=[PROFILE|{'api_key':'private-fixture-key'}]
        response=self.client.put('/api/admin/settings',json=payload,headers=admin['headers'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(db.secret('provider_baseline'),'private-fixture-key')
        self.assertNotIn('private-fixture-key',json.dumps(db.query('SELECT * FROM settings')))
        reply=self.client.get('/api/admin/settings',headers=admin['headers']).json()
        self.assertTrue(reply['additional_models'][0]['key_configured'])
        self.assertNotIn('api_key',reply['additional_models'][0])
        payload['additional_models']=[PROFILE]
        self.assertEqual(self.client.put('/api/admin/settings',json=payload,headers=admin['headers']).status_code,200)
        self.assertEqual(db.secret('provider_baseline'),'private-fixture-key')
        normal=self.user('normal@example.test')
        self.assertEqual(self.client.put('/api/admin/settings',json=payload,headers=normal['headers']).status_code,403)

    def test_profile_validation_prevents_bad_budget_or_duplicate_ids(self):
        admin=self.user(role='admin')
        for profiles in ([PROFILE,PROFILE],[PROFILE|{'id':'team'}],[PROFILE|{'context_window':8192}],
                         [PROFILE|{'context_window':262144}],[PROFILE|{'base_url':'https://user:pass@example.test/v1'}]):
            with self.subTest(profiles=profiles):
                reply=self.client.put('/api/admin/settings',json=dict(db.DEFAULTS,additional_models=profiles),headers=admin['headers'])
                self.assertEqual(reply.status_code,422,reply.text)


if __name__=='__main__': unittest.main()
