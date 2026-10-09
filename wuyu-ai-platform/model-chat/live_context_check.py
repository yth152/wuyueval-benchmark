"""Manual regression: real model, eight searches, and a synthetic long attachment.

Consumes model/search quota; never uses the user's documents or prints keys.
"""
import asyncio
import json
from pathlib import Path

import httpx

OUT = Path('output/context-checks')


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    document = ('这是一份合成测试材料，不是政策依据。固体废物管理需要区分产生、收集、贮存、运输和处置环节。'
                '实际结论应依据公开政策或论文；这里的重复段落仅用于检验长附件和搜索结果能否同时进入上下文。\n' * 180)[:12000]
    question = ('请为固废监管研究制作一个四行的公开证据索引。必须分别检索以下四个方向，每个方向单独调用一次工具，'
                '每次同时包含中文和英文关键词，共四组中英检索：'
                '①用联网搜索查生活垃圾分类官方政策；②用联网搜索查退役动力电池回收监管政策；'
                '③用学术搜索查固废堆场遥感识别研究；④用学术搜索查污泥资源化研究。'
                '搜索完四组后立即整理最终答案，每个方向只选一个来源，用四行表格简述并引用[S编号]，总计不超过350字。'
                '附件是合成容量测试材料，不作为事实来源，不需复述。')
    events = []
    async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
        uploaded = await client.post('http://127.0.0.1:8910/api/files',
            files={'file': ('context-fixture-12000.txt', document.encode(), 'text/plain')})
        uploaded.raise_for_status()
        file = uploaded.json()
        async with client.stream('POST', 'http://127.0.0.1:8910/api/chat', json={
            'provider': 'team', 'messages': [{'role': 'user', 'content': question, 'file_ids': [file['id']]}],
            'network_search': True, 'thinking': True, 'max_tokens': 8192}) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith('data: '):
                    continue
                event = json.loads(line[6:])
                events.append(event)
                if event['type'] in {'model_round', 'warning', 'error'}:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                if event['type'] == 'tool_result':
                    print(json.dumps({k: event.get(k) for k in ('type', 'language', 'ok', 'returned_count')}, ensure_ascii=False), flush=True)
    (OUT / 'eight-searches-long-attachment.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
    final = next((e for e in reversed(events) if e['type'] in {'done', 'error'}), {})
    citations = next((e for e in events if e['type'] == 'citations'), {})
    report = {'attachment_characters': len(document), 'finish': final,
              'citation_count': len(citations.get('sources', [])),
              'unmatched_citations': citations.get('unmatched_ids', []),
              'search_results': sum(e['type'] == 'tool_result' for e in events)}
    (OUT / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not (final.get('complete') and final.get('search_calls') == 8 and report['citation_count'] > 0
            and not report['unmatched_citations']):
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
