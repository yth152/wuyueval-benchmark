"""Small synthetic image probe; never uses user files."""
import asyncio, base64, io, json
from pathlib import Path
import httpx
from PIL import Image
from credentials import PROVIDERS, api_key

async def probe(name):
    data=io.BytesIO(); Image.new('RGB',(64,64),(255,0,0)).save(data,format='PNG')
    p=PROVIDERS[name]
    payload={'model':p['model'],'messages':[{'role':'user','content':[
        {'type':'text','text':'图片的主要颜色是什么？只回答颜色。'},
        {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode()}}]}],
        'max_tokens':128,'temperature':0,'chat_template_kwargs':{'enable_thinking':False}}
    if name=='baseline':payload['enable_thinking']=False
    async with httpx.AsyncClient(timeout=60,trust_env=False) as client:
        r=await client.post(p['base_url']+'/chat/completions',headers={'Authorization':'Bearer '+api_key(name)},json=payload)
    value={'provider':name,'status':r.status_code}
    if r.status_code==200:
        d=r.json();value['answer']=d['choices'][0]['message'].get('content');value['model']=d.get('model')
    else:value['detail']=r.text[:500]
    return value

async def main():
    results=await asyncio.gather(*(probe(n) for n in PROVIDERS))
    Path('output/vision-probes.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False))
if __name__=='__main__':asyncio.run(main())
