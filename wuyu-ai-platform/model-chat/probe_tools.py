"""Explicit live compatibility probe; no search is executed here."""
import asyncio
import json
from pathlib import Path
import httpx
from credentials import PROVIDERS, api_key, metaso_key

TOOLS = [{"type": "function", "function": {
    "name": "metaso_web_search", "description": "Search the web for current factual information. Returns up to ten sources.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
}}]

async def probe(provider):
    cfg = PROVIDERS[provider]
    key = api_key(provider)
    payload = {"model": cfg['model'], "messages": [{"role":"user", "content":"请用网页搜索工具查询生态环境部最近的固体废物污染防治相关通知。"}],
               "tools": TOOLS, "tool_choice": "auto", "temperature": 0.1, "max_tokens": 2048, "stream": False,
               "chat_template_kwargs": {"enable_thinking": True}}
    if provider == 'baseline': payload['enable_thinking'] = True
    async with httpx.AsyncClient(timeout=100, trust_env=False) as client:
        response = await client.post(cfg['base_url'] + '/chat/completions', headers={'Authorization':'Bearer ' + key}, json=payload)
    result = {'provider': provider, 'status': response.status_code}
    if response.status_code == 200:
        obj = response.json(); choice = obj.get('choices',[{}])[0]; msg = choice.get('message', {})
        result.update(model=obj.get('model'), finish_reason=choice.get('finish_reason'), tool_calls=msg.get('tool_calls'),
                      content_excerpt=(msg.get('content') or '')[:700], reasoning_chars=len(msg.get('reasoning') or msg.get('reasoning_content') or ''))
    else:
        result['error'] = response.text[:900].replace(key, '[redacted]')
    path = Path('output/tool-probes');path.mkdir(parents=True,exist_ok=True)
    (path/(provider+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)

async def main():
    try: print(json.dumps({'metaso_configured': bool(metaso_key())}), flush=True)
    except Exception: print('{"metaso_configured": false}', flush=True)
    await asyncio.gather(*(probe(name) for name in PROVIDERS))
if __name__ == '__main__': asyncio.run(main())
