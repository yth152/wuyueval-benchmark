"""Explicit live integration checks; these consume real model/search usage."""
import asyncio
import json
from pathlib import Path
import httpx

CASES = [
    ('offline', 'team', False, False, '17乘以23等于多少？只输出数字。', set()),
    ('auto_direct', 'team', True, True, '17乘以23等于多少？只输出数字。', set()),
    ('team_two_tools', 'team', True, True,
     '请找一条生态环境部关于遥感排查固体废物的公开资料和一篇遥感识别非法倾倒点的研究论文，各给出一条发现，正文引用来源，200字内。',
     {'web_search', 'scholar_search'}),
    ('baseline_scholar', 'baseline', True, True,
     '请检索关于遥感识别非法垃圾倾倒点的学术研究，选一篇论文给出题目、年份和一条研究发现，正文引用来源，150字内。',
     {'scholar_search'}),
]


async def check(case):
    name, provider, online, thinking, question, expected = case
    events = []
    print(json.dumps({'case': name, 'state': 'started'}), flush=True)
    async with httpx.AsyncClient(timeout=240, trust_env=False) as client:
        async with client.stream('POST', 'http://127.0.0.1:8910/api/chat', json={
            'provider': provider, 'network_search': online, 'thinking': thinking,
            'temperature': 0.1, 'max_tokens': 4096,
            'messages': [{'role': 'user', 'content': question}],
        }) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith('data: '):
                    data = json.loads(line[6:]); events.append(data)
                    if data['type'] in {'tool_start', 'tool_result', 'error'}:
                        print(json.dumps({'case': name, 'event': data['type'], 'name': data.get('name'),
                                          'query': data.get('query'), 'ok': data.get('ok'),
                                          'count': len(data.get('sources', [])), 'message': data.get('message')}, ensure_ascii=False), flush=True)
    output = Path('output/agent-checks'); output.mkdir(parents=True, exist_ok=True)
    (output/(name+'.json')).write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
    calls = [e for e in events if e['type'] == 'tool_start']
    names = {e['name'] for e in calls}
    citations = next((e for e in reversed(events) if e['type'] == 'citations'), {})
    summary = {'case': name, 'tools': sorted(names), 'search_calls': len(calls),
               'citations': len(citations.get('sources', [])), 'final': events[-1]}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    assert events[-1]['type'] == 'done' and events[-1]['complete'], summary
    assert names == expected, summary
    if expected:
        assert citations.get('sources') and not citations.get('unmatched_ids'), summary
        assert all(e['ok'] and len(e['sources']) <= 10 for e in events if e['type'] == 'tool_result'), summary
    else:
        assert not calls and events[-1]['search_calls'] == 0, summary
    return {**summary, 'passed': True}


async def main():
    summaries = []
    for batch in (CASES[:2], CASES[2:]):
        results = await asyncio.gather(*(check(case) for case in batch), return_exceptions=True)
        summaries.extend(result if isinstance(result, dict) else {'passed': False, 'error': str(result)} for result in results)
    Path('output/agent-checks/summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding='utf-8')
    assert all(s['passed'] for s in summaries), 'One or more live checks failed; inspect output/agent-checks.'

if __name__ == '__main__': asyncio.run(main())
