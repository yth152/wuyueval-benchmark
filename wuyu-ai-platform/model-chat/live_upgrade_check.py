"""Smoke tests for bilingual native tools and attachments, using synthetic fixtures."""
import asyncio,json
from pathlib import Path
import httpx
from test_attachments import fixtures

OUT=Path('output/upgrade-checks')
OUT.mkdir(parents=True,exist_ok=True)
for name,data in fixtures().items():(OUT/name).write_bytes(data)

async def query(name,provider,text,network):
    events=[]
    async with httpx.AsyncClient(timeout=240,trust_env=False) as client:
        async with client.stream('POST','http://127.0.0.1:8910/api/chat',json={'provider':provider,'messages':[{'role':'user','content':text}], 'thinking':False,'network_search':network,'max_tokens':1400}) as response:
            if response.status_code!=200:raise RuntimeError(await response.aread())
            async for line in response.aiter_lines():
                if line.startswith('data: '):events.append(json.loads(line[6:]))
    (OUT/(name+'.json')).write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
    done=next((e for e in reversed(events) if e['type'] in {'error','done'}),{})
    tools=[{k:e.get(k) for k in ('name','language','query','ok','date_window','returned_count')} for e in events if e['type']=='tool_result']
    print(json.dumps({'case':name,'finish':done,'searches':tools,'citations':len(next((e['sources'] for e in events if e['type']=='citations'),[]))},ensure_ascii=False),flush=True)
    return bool(done.get('complete'))

async def main():
    passed=await asyncio.gather(
        query('team-bilingual','team','请检索固废遥感监管的一条政府公开资料和一篇近十年的研究论文。先拆出中英文关键词，用联网搜索和学术搜索各检索一组，各选一条证据，用两句话回答并引用来源。',True),
        query('baseline-bilingual','baseline','请搜索近十年卫星遥感识别非法固废堆场的论文。先拆成中英文关键词分别检索，只简短介绍一篇并标注引用。',True),
        query('auto-direct','team','17乘23是多少？只回答数字。',True))
    if not all(passed):raise SystemExit(1)
if __name__=='__main__':asyncio.run(main())
