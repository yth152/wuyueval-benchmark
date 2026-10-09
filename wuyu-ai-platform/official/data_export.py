"""Administrator JSON export: a consistent database snapshot, explicit fields.

Write a private temporary file, then stream the download without buffering an
entire account in memory. Authentication/configuration secrets are never read.
"""
import base64
import json
import os
import tempfile
import time
from datetime import datetime, timezone
import store as db


def remove_export(path):
    path.unlink(missing_ok=True)


def build_export(scope, user_ids, include_attachments, actor):
    import attachments
    folder = db.DATA / 'exports'; folder.mkdir(exist_ok=True)
    for pattern in ('wuyu-export-*.json','wuyu-personal-*.json'):
        for stale in folder.glob(pattern):
            if stale.stat().st_mtime < time.time()-86400: stale.unlink(missing_ok=True)
    handle, name = tempfile.mkstemp(prefix='wuyu-export-', suffix='.json', dir=folder)
    from pathlib import Path
    path = Path(name); os.chmod(path, 0o600)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as out, db.connect() as conn:
            conn.execute('BEGIN')  # One consistent snapshot, including active turns.
            targets = sorted(set(user_ids))
            where = '' if scope == 'all' else ' WHERE id IN (' + ','.join('?' for _ in targets) + ')'
            users = conn.execute('SELECT id,email,name,organization,identity,role,active,created FROM users'+where+' ORDER BY created,id', () if scope=='all' else targets).fetchall()
            if scope == 'selected' and len(users) != len(targets): raise ValueError('部分用户已不存在，请刷新用户列表后重试。')
            def value(obj): out.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')))
            def array(rows, render):
                out.write('[')
                for i,row in enumerate(rows):
                    if i: out.write(',')
                    render(dict(row))
                out.write(']')
            manifest = {'schema':'wuyu-user-data', 'version':1, 'exported_at':datetime.now(timezone.utc).isoformat(),
                'scope':scope, 'user_count':len(users), 'include_attachment_content':include_attachments,
                'notes':['包含已从历史列表移除的对话；生成中的任务为导出时的快照。',
                         '附件内容为服务器保留的解析文本与规范化 JPEG；原始 Office/PDF 文件不在现有存储中。',
                         '不含密码及散列、登录会话、验证码、模型/搜索密钥、服务器配置或内部请求缓存。',
                         '历史摘要为提取式摘录；完整问答保存在 turns 中。']}
            out.write('{"manifest":');value(manifest);out.write(',"users":')
            def render_user(user):
                uid = user['id'];out.write('{"profile":');value(user)
                out.write(',"preferences":')
                pref=conn.execute('SELECT value FROM user_preferences WHERE user_id=?',(uid,)).fetchone()
                value(json.loads(pref['value']) if pref else {'theme':'light','font_size':'standard','send_key':'enter'})
                out.write(',"voice_calls":')
                if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='voice_calls'").fetchone():
                    array(conn.execute('SELECT id,created,seconds,status FROM voice_calls WHERE user_id=? ORDER BY created,id', (uid,)),value)
                else: value([])
                out.write(',"conversations":')
                def render_chat(chat):
                    cid = chat['id'];out.write('{"conversation":');value(chat);out.write(',"turns":')
                    def render_turn(row):
                        turn = json.loads(row.pop('turn'));turn.pop('_usage',None)
                        value({'task':row,'conversation_turn':turn})
                    array(conn.execute('SELECT id,status,turn,created,started,finished,reserved,charged,day,revision FROM jobs WHERE user_id=? AND conversation_id=? ORDER BY created,id', (uid,cid)),render_turn)
                    out.write(',"memory_preferences":')
                    pref = conn.execute('SELECT mode,auto_compress FROM memory_preferences WHERE user_id=? AND conversation_id=?', (uid,cid)).fetchone()
                    value(dict(pref) if pref else {'mode':'auto','auto_compress':True})
                    out.write(',"memory_summaries":')
                    def render_summary(row):
                        row['content'] = json.loads(row['content']);value(row)
                    array(conn.execute('SELECT id,watermark,budget,content,token_estimate,created,method FROM memory_summaries WHERE user_id=? AND conversation_id=? ORDER BY id', (uid,cid)),render_summary)
                    out.write(',"memory_chunks":')
                    def render_chunk(row):
                        if not include_attachments and row['kind'] == 'attachment': row.pop('text',None)
                        value(row)
                    array(conn.execute('SELECT id,job_id,kind,file_id,position,text,created FROM memory_chunks WHERE user_id=? AND conversation_id=? ORDER BY id', (uid,cid)),render_chunk)
                    out.write('}')
                array(conn.execute('SELECT id,title,created,updated,deleted FROM conversations WHERE user_id=? ORDER BY created,id', (uid,)),render_chat)
                out.write(',"attachments":')
                def render_file(row):
                    try: item = attachments.read_file(row['id'])
                    except (ValueError,OSError):
                        value({**row,'available':False});return
                    fields = ('id','name','size','kind','mime','characters','width','height','warning')
                    metadata = {key:item[key] for key in fields if key in item}
                    metadata.update(created=row['created'],available=True)
                    out.write('{"metadata":');value(metadata)
                    if include_attachments:
                        out.write(',"parsed_text":');value(item.get('text'))
                        if item.get('kind') == 'image':
                            img = attachments.UPLOAD_DIR / (row['id']+'.jpg')
                            out.write(',"image_mime":"image/jpeg","image_base64":')
                            if img.is_file():
                                out.write('"')
                                with img.open('rb') as raw:
                                    while chunk := raw.read(3*131072): out.write(base64.b64encode(chunk).decode('ascii'))
                                out.write('"')
                            else: out.write('null')
                    out.write('}')
                array(conn.execute('SELECT id,size,created FROM files WHERE user_id=? ORDER BY created,id', (uid,)),render_file)
                out.write('}')
            array(users,render_user);out.write('}')
        db.audit(actor,'export_user_json:'+json.dumps({'scope':scope,'user_ids':[u['id'] for u in users],'attachments':include_attachments},separators=(',',':')))
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
