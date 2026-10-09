"""Explicit, billable smoke check. Never runs during normal app startup."""
import asyncio
import json
import time
from pathlib import Path
import httpx


async def check(provider):
    start = time.monotonic()
    events = []
    async with httpx.AsyncClient(timeout=240, trust_env=False) as client:
        async with client.stream('POST', 'http://127.0.0.1:8910/api/chat', json={
            'provider': provider, 'thinking': True, 'max_tokens': 2048,
            'messages': [{'role': 'user', 'content': '17 乘以 23 等于多少？最终回答只需一句话。'}],
        }) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith('data: '): events.append(json.loads(line[6:]))
    answer = ''.join(e.get('text', '') for e in events if e['type'] == 'content')
    reasoning = ''.join(e.get('text', '') for e in events if e['type'] == 'reasoning')
    result = {'provider': provider, 'wall_seconds': round(time.monotonic() - start, 2),
              'answer': answer, 'reasoning_chars': len(reasoning), 'final': events[-1] if events else None}
    output = Path(__file__).parent / 'output'
    output.mkdir(exist_ok=True)
    (output / (provider + '-live-check.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    assert result['final']['type'] == 'done' and result['final']['complete'], result['final']
    assert '391' in answer, answer


async def main(): await asyncio.gather(check('team'), check('baseline'))
if __name__ == '__main__': asyncio.run(main())
