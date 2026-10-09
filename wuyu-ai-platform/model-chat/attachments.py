"""Local attachment storage and bounded model context."""
import asyncio
import base64
import json
import os
import re
import sys
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent
UPLOAD_DIR=ROOT/'runtime/uploads'
DOCUMENT_EXT={'.docx','.xlsx','.xls','.pptx','.pdf','.txt','.md','.csv','.tsv','.json','.log'}
IMAGE_EXT={'.png','.jpg','.jpeg','.webp','.gif','.bmp'}
FILE_MB=20
IMAGE_MB=10
TOTAL_MB=50
MAX_FILES=5
STORAGE_MB=500
PARSER_SLOTS=asyncio.Semaphore(2)
PUBLIC_FIELDS=('id','name','size','kind','warning','characters','width','height','preview_url')

def config():
    return {'max_files':MAX_FILES,'file_mb':FILE_MB,'image_mb':IMAGE_MB,'total_mb':TOTAL_MB,
            'supported':sorted(DOCUMENT_EXT|IMAGE_EXT)}

def read_file(file_id):
    if not re.fullmatch(r'[a-f0-9]{32}',file_id): raise ValueError('附件编号无效。')
    try:return json.loads((UPLOAD_DIR/(file_id+'.json')).read_text(encoding='utf-8'))
    except (OSError,ValueError): raise ValueError('附件已不存在，请重新上传。') from None

def public_file(item):return {key:item[key] for key in PUBLIC_FIELDS if key in item}


def model_image_bytes(item):
    try:
        data=(UPLOAD_DIR/(item['id']+'.jpg')).read_bytes()
        if not data.startswith(b'\xff\xd8'): raise ValueError('Invalid stored JPEG')
        # /tokenize-first poisoned the deployed service's image cache. Give
        # model requests a stable new byte identity without changing any pixels,
        # dimensions, stored previews or file metadata. Never tokenize this copy.
        comment=b'wuyu-vision-chat-v3'
        marker=b'\xff\xfe'+(len(comment)+2).to_bytes(2,'big')+comment
        return data[:2]+marker+data[2:]
    except (OSError,ValueError):
        raise ValueError('图片已不存在或无法读取，请重新上传。') from None

async def save_file(name,data):
    name=re.sub(r'[\x00-\x1f]','',str(name).replace('\\','/').split('/')[-1])[:160] or '附件'
    ext=Path(name).suffix.lower()
    if ext not in DOCUMENT_EXT|IMAGE_EXT:
        raise ValueError('支持 DOCX、PPTX、XLSX、XLS、PDF、文本和常见图片；旧版 DOC/PPT 请另存为 DOCX/PPTX。')
    limit=(IMAGE_MB if ext in IMAGE_EXT else FILE_MB)*1024*1024
    if not data or len(data)>limit:raise ValueError(f'文件为空或超过单个文件限制（{limit//1024//1024} MB）。')
    async with PARSER_SLOTS:
        UPLOAD_DIR.mkdir(parents=True,exist_ok=True)
        files=list(UPLOAD_DIR.iterdir())
        if len(files)>2000 or sum(p.stat().st_size for p in files if p.is_file())+len(data)>STORAGE_MB*1024*1024:
            raise ValueError('附件存储空间已满，请联系管理员整理后重试。')
        # Uploaded documents are untrusted input.  Do not expose model/search
        # credentials to the parser process, and keep its environment stable
        # enough for temporary-file and Python I/O handling.
        worker_env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'TEMP', 'TMP', 'PYTHONIOENCODING') if key in os.environ}
        proc=await asyncio.create_subprocess_exec(sys.executable,str(ROOT/'attachment_worker.py'),name,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            env=worker_env)
        try:
            out,_=await asyncio.wait_for(proc.communicate(data),timeout=45)
            try:
                parsed=json.loads(out)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError('文件解析失败，请确认文件未损坏或拆分后重试。') from None
            if proc.returncode or parsed.get('error'):raise ValueError(parsed.get('error','文件解析失败。'))
        except (TimeoutError,asyncio.CancelledError):
            if proc.returncode is None:proc.kill();await proc.wait()
            if asyncio.current_task().cancelling():raise
            raise ValueError('解析超过 45 秒，请拆分文件。') from None
        file_id=uuid.uuid4().hex
        item={'id':file_id,'name':name,'size':len(data),**parsed}
        if parsed['kind']=='image':
            (UPLOAD_DIR/(file_id+'.jpg')).write_bytes(base64.b64decode(item.pop('image')))
            item['preview_url']='/api/files/'+file_id+'/preview'
        else:item['characters']=len(item['text'])
        (UPLOAD_DIR/(file_id+'.json')).write_text(json.dumps(item,ensure_ascii=False),encoding='utf-8')
        return public_file(item)

def prepare_messages(body):
    """Files remain attached to their originating user turn, including follow-ups."""
    prepared=[];notices=[]
    for message in body.messages:
        files=[read_file(fid) for fid in message.file_ids]
        if sum(f['size'] for f in files)>TOTAL_MB*1024*1024:raise ValueError('每轮附件合计不能超过 50 MB。')
        text=message.content
        docs=[f for f in files if f['kind']=='document']
        per_file=min(12000,24000//max(1,len(docs)))
        for f in docs:
            excerpt=f['text'][:per_file]
            text+=f'\n\n<attachment name={json.dumps(f["name"],ensure_ascii=False)}>\n{excerpt}\n</attachment>'
            if f.get('warning'):text+='\n解析说明：'+f['warning']
            if len(f['text'])>per_file:
                note=f'《{f["name"]}》本轮仅选取前 {per_file:,} 字符（全文 {len(f["text"]):,}），可拆分后继续分析。'
                text+='\n'+note;notices.append(note)
        images=[f for f in files if f['kind']=='image']
        prepared.append({'role':message.role,'text':text,'images':images})
    while len(prepared)>1 and (sum(len(m['text']) for m in prepared)+len(body.system)>60000 or sum(len(m['images']) for m in prepared)>5):
        prepared=prepared[2:]
        if '较早的完整问答及附件已移出上下文，以保留本次问题和附件。' not in notices:notices.append('较早的完整问答及附件已移出上下文，以保留本次问题和附件。')
    result=[]
    for m in prepared:
        content=m['text']
        if m['images']:
            content=[{'type':'text','text':content}]
            for f in m['images']:
                data=model_image_bytes(f)
                content.extend([{'type':'text','text':'用户附件图片：'+f['name']},
                    {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(data).decode()}}])
        result.append({'role':m['role'],'content':content})
    return result,list(dict.fromkeys(notices))
