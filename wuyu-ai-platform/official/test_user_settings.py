import json
import unittest

import store as db
import test_official as fixtures


class UserSettingsTests(unittest.TestCase):
    setUp=fixtures.PlatformTests.setUp
    tearDown=fixtures.PlatformTests.tearDown
    user=fixtures.PlatformTests.user
    chat=fixtures.PlatformTests.chat
    submit=fixtures.PlatformTests.submit
    wait_done=fixtures.PlatformTests.wait_done

    def test_preferences_are_persistent_validated_and_account_scoped(self):
        a=self.user();b=self.user('b@example.test')
        prefs={'theme':'dark','font_size':'larger','send_key':'ctrl_enter'}
        response=self.client.put('/api/me/preferences',headers=a['headers'],json=prefs)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/me/preferences',headers=a['headers']).json(),prefs)
        self.assertEqual(self.client.get('/api/me/preferences',headers=b['headers']).json()['theme'],'light')
        self.assertEqual(json.loads(db.query('SELECT value FROM user_preferences WHERE user_id=?',(a['id'],),one=True)['value']),prefs)
        for body in (prefs|{'theme':'invalid'},prefs|{'user_id':b['id']},prefs|{'font_size':'100'}):
            self.assertEqual(self.client.put('/api/me/preferences',headers=a['headers'],json=body).status_code,422)

    def test_profile_updates_only_own_allowed_fields(self):
        a=self.user();b=self.user('b@example.test')
        profile={'name':' 新姓名 ','organization':' 新机构 ','identity':'企业'}
        r=self.client.patch('/api/me/profile',headers=a['headers'],json=profile)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['name'],'新姓名')
        self.assertEqual(r.json()['role'],'user');self.assertEqual(r.json()['email'],'a@example.test')
        self.assertEqual(self.client.get('/api/me',headers=b['headers']).json()['name'],'测试用户')
        for extra in ({'role':'admin'},{'email':'other@example.test'},{'user_id':b['id']},{'name':' '},{'identity':'invalid'}):
            self.assertEqual(self.client.patch('/api/me/profile',headers=a['headers'],json=profile|extra).status_code,422)

    def test_personal_export_and_usage_cannot_read_other_accounts(self):
        a=self.user();b=self.user('b@example.test');cid=self.chat(a);self.chat(b)
        r=self.submit(a,cid,message='仅属于A的问答');self.wait_done(r.json()['id'])
        usage=self.client.get('/api/me/data',headers=a['headers']).json()
        self.assertEqual(usage['today']['questions'],1);self.assertEqual(usage['today']['charged'],30)
        self.assertEqual(self.client.get('/api/me/data',headers=b['headers']).json()['today']['questions'],0)
        wrong=self.client.post('/api/me/exports',headers=a['headers'],json={'user_ids':[b['id']]})
        self.assertEqual(wrong.status_code,422)
        result=self.client.post('/api/me/exports',headers=a['headers'],json={})
        self.assertEqual(result.status_code,200,result.text);url=result.json()['download_url']
        self.assertEqual(self.client.get(url,headers=b['headers']).status_code,404)
        download=self.client.get(url,headers=a['headers']);self.assertEqual(download.status_code,200)
        exported=download.json();self.assertEqual(exported['manifest']['user_count'],1)
        self.assertEqual(exported['users'][0]['profile']['id'],a['id'])
        self.assertIn('preferences',exported['users'][0])
        for forbidden in ('b@example.test','password_hash','test-secret-not-real','model_secret'):
            self.assertNotIn(forbidden,download.text)
        self.assertEqual(self.client.get('/api/admin/settings',headers=a['headers']).status_code,403)

    def test_personal_endpoints_need_login(self):
        for path in ('preferences','data','exports/'+'a'*32):self.assertEqual(self.client.get('/api/me/'+path).status_code,401)
        for method,path,body in [('put','preferences',{}),('post','exports',{}),('patch','profile',{'name':'n','organization':'o','identity':'企业'})]:
            self.assertEqual(getattr(self.client,method)('/api/me/'+path,json=body).status_code,401)


if __name__=='__main__':unittest.main()
