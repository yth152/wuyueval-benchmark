"""Disposable localhost UI fixture, never imported by the formal entry point."""
import asyncio
import os
import tempfile
import time
import uuid
from pathlib import Path

if __name__ == '__main__':
    root=Path(__file__).resolve().parent
    (root/'output').mkdir(exist_ok=True)
    data=Path(os.environ['WUYU_QA_DATA']).resolve() if os.environ.get('WUYU_QA_DATA') else Path(tempfile.mkdtemp(prefix='ui-qa-',dir=root/'output'))
    if data.parent != (root/'output').resolve() or not data.name.startswith('ui-qa-'): raise RuntimeError('UI fixture must use an isolated output/ui-qa-* directory')
    os.environ['WUYU_DATA']=str(data)
    os.environ['WUYU_ALLOWED_ORIGINS']='http://127.0.0.1:8921'
    import store as db
    import auth
    auth.COOKIE='wuyu_qa_session'
    import app_main
    import uvicorn
    from jobs import Scheduler
    db.init()
    db.save_secret('model','ui-fixture');db.save_secret('search','ui-fixture')
    db.save_secret('provider_baseline','ui-fixture')
    db.save_settings({'additional_models':[{'id':'baseline','display_name':'在线 Qwen 27B',
        'model':'qwen3.8-27b','base_url':'https://example.test/v1','context_window':32768,
        'protocol':'baseline','enabled':True}],'max_output_tokens':16384},'ui-fixture')
    # Disposable accounts only, never created in the real application's data.
    for email,name,role in [('ui-admin@example.test','界面测试管理员','admin'),('ui-user@example.test','界面测试用户','user')]:
        if db.query('SELECT id FROM users WHERE email=?',(email,),one=True): continue
        uid=uuid.uuid4().hex
        db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,1,?)',(uid,email,name,'无隅界面测试','科研院所',role,time.time()))
        db.execute('INSERT INTO user_passwords VALUES(?,?,?)',(uid,auth.hash_password('Ui-fixture-only-2026!'),time.time()))
    async def answer(row,output):
        yield {'type':'reasoning','text':'界面联调示例：检查后台执行、滚动、附件及引用展示。此内容由本地测试程序生成，未调用真实模型。\n'*70}
        for _ in range(60):await asyncio.sleep(.25)
        sources=[{'source_id':'S1','title':'固废管理资料（界面测试来源）','url':'https://example.test/web','snippet':'用于验证网页引用侧栏。','date':'2026-10-06','type':'webpage','authors':[]},
                 {'source_id':'S2','title':'Resource circulation research（界面测试文献）','url':'https://example.test/paper','snippet':'用于验证文献引用与网页分组。','date':'2025-01-01','type':'scholar','authors':['测试作者']}]
        yield {'type':'tool_start','name':'web_search','call_id':'test-web','query':'测试来源','language':'zh'}
        yield {'type':'tool_result','name':'web_search','call_id':'test-web','ok':True,'sources':sources[:1]}
        yield {'type':'tool_start','name':'scholar_search','call_id':'test-paper','query':'test sources','language':'en'}
        yield {'type':'tool_result','name':'scholar_search','call_id':'test-paper','ok':True,'sources':sources[1:]}
        yield {'type':'content','text':'## 界面联调示例\n\n这段回答用于验证后台任务和页面展示，**没有调用真实模型**。\n\n- 切换对话后，任务继续在服务器执行。[S1]\n- 文献和网页分别展示，并可定位正文引用。[S2]\n\n返回历史对话即可查看已保存的结果。'}
        yield {'type':'citations','sources':sources,'unmatched_ids':[]}
        yield {'type':'done','complete':True,'seconds':15,'usage':{'prompt_tokens':100,'completion_tokens':300,'total_tokens':400}}
    app_main.scheduler=Scheduler(answer)
    print('Isolated UI data: '+str(data),flush=True)
    uvicorn.run(app_main.app,host='127.0.0.1',port=8921,access_log=False)
