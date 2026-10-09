"""Authenticated streaming speech input. PCM stays in memory, keys stay in vault."""
import asyncio
import json
import time
import uuid
from contextlib import AsyncExitStack, suppress

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, SecretStr
import auth
import store as db
import xfyun_asr

router = APIRouter()
DEFAULT = {'enabled': False, 'max_seconds': 120, 'concurrency': 2, 'daily_sessions': 60}
active = set()


def init():
    db.execute('CREATE TABLE IF NOT EXISTS voice_calls(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,created REAL NOT NULL,seconds REAL DEFAULT 0,status TEXT NOT NULL)')


def config():
    row = db.query("SELECT value FROM settings WHERE key='voice_config'", one=True)
    return DEFAULT | (json.loads(row['value']) if row else {})


def public_config():
    cfg = config()
    return {'ready': cfg['enabled'] and all(db.secret('voice_' + key) for key in ('app_id', 'api_key', 'api_secret')),
            'max_seconds': cfg['max_seconds'], 'streaming': True}


class VoiceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    max_seconds: int = Field(default=120, ge=10, le=120)
    concurrency: int = Field(default=2, ge=1, le=8)
    daily_sessions: int = Field(default=60, ge=1, le=1000)
    app_id: SecretStr = Field(default_factory=lambda: SecretStr(''), max_length=256)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''), max_length=256)
    api_secret: SecretStr = Field(default_factory=lambda: SecretStr(''), max_length=256)


@router.get('/api/admin/voice')
def admin_config(user=Depends(auth.admin)):
    return config() | {'configured': {k: bool(db.secret('voice_'+k)) for k in ('app_id','api_key','api_secret')},
                       'usage': db.query('SELECT COUNT(*) AS calls,COALESCE(SUM(seconds),0) AS seconds FROM voice_calls', one=True)}


@router.put('/api/admin/voice')
def save_config(body: VoiceInput, user=Depends(auth.admin)):
    for key in ('app_id','api_key','api_secret'):
        value = getattr(body, key).get_secret_value().strip()
        if value and (len(value) < 4 or any(ord(c) < 33 or ord(c) > 126 for c in value)): raise HTTPException(422, '请填写原始讯飞凭据，不含空格或换行。')
    if body.enabled and not all(getattr(body, k).get_secret_value().strip() or db.secret('voice_'+k) for k in ('app_id','api_key','api_secret')):
        raise HTTPException(422, '启用前请完整配置 App ID、API Key 和 API Secret。')
    for key in ('app_id','api_key','api_secret'): db.save_secret('voice_'+key, getattr(body,key).get_secret_value().strip())
    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('voice_config', json.dumps(body.model_dump(exclude={'app_id','api_key','api_secret'}))))
    db.audit(user['id'], 'update_voice')
    return {'ok': True}


@router.websocket('/api/voice/stream')
async def stream(browser: WebSocket):
    # HTTP middleware doesn't cover WebSockets. Repeat origin/session enforcement.
    from app_main import ORIGINS, HOSTS
    try: user = auth.current(browser)
    except HTTPException:
        await browser.close(code=4401); return
    if browser.headers.get('origin') not in ORIGINS or browser.headers.get('host') not in HOSTS:
        await browser.close(code=4403); return
    await browser.accept()
    cfg = config(); uid = user['id']; claimed = False; call_id = None
    stats = {'seconds': 0, 'status': 'interrupted'}
    try:
        if not public_config()['ready']: raise ValueError('语音服务尚未启用，请联系管理员。')
        if uid in active or len(active) >= cfg['concurrency']: raise ValueError('语音服务正在使用中，请稍后再试。')
        active.add(uid); claimed = True
        call_id = uuid.uuid4().hex
        # Sliding 24-hour limit includes failed attempts, preventing handshake abuse.
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            count = conn.execute('SELECT COUNT(*) FROM voice_calls WHERE user_id=? AND created>?', (uid,time.time()-86400)).fetchone()[0]
            if count >= cfg['daily_sessions']: raise ValueError('已达到每日语音次数上限，请稍后再试。')
            conn.execute('INSERT INTO voice_calls(id,user_id,created,status) VALUES(?,?,?,?)', (call_id,uid,time.time(),'running'))
        credentials = {k: db.secret('voice_'+k) for k in ('app_id','api_key','api_secret')}
        async with asyncio.timeout(cfg['max_seconds']+45), AsyncExitStack() as stack:
            tasks = []
            try:
                waiting = asyncio.create_task(browser.receive())
                opening = asyncio.create_task(stack.enter_async_context(xfyun_asr.open_session(credentials)))
                tasks = [waiting, opening]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if waiting in done:
                    if waiting.result()['type'] == 'websocket.disconnect': raise WebSocketDisconnect()
                    raise ValueError('请等待语音连接就绪后再发送录音。')
                upstream, session_id = await opening
                waiting.cancel(); await asyncio.gather(waiting, return_exceptions=True)
                await browser.send_json({'type': 'ready', 'max_seconds': cfg['max_seconds']})
                ended = asyncio.Event(); segments = xfyun_asr.Segments()

                async def upload():
                    pacer = xfyun_asr.PCMSender(upstream); sent = 0; checked = 0
                    while True:
                        packet = await asyncio.wait_for(browser.receive(), 25 if ended.is_set() else 15)
                        if packet['type'] == 'websocket.disconnect': raise WebSocketDisconnect()
                        if time.monotonic() - checked > 2:
                            auth.current(browser)
                            if not config()['enabled']: raise ValueError('管理员已关闭语音服务。')
                            checked = time.monotonic()
                        if ended.is_set(): raise ValueError('录音已结束，请等待识别结果。')
                        if packet.get('bytes') is not None:
                            audio = packet['bytes']
                            if not audio or len(audio) > 1280 or len(audio)%2: raise ValueError('录音数据格式无效。')
                            if sent+len(audio) > cfg['max_seconds']*32000: raise ValueError('录音超过时长上限。')
                            await pacer.send(audio); sent += len(audio); stats['seconds'] = round(sent/32000, 3)
                        elif packet.get('text'):
                            if len(packet['text']) > 100 or json.loads(packet['text']) != {'type':'end'} or not sent: raise ValueError('请录音后结束，或取消本次录音。')
                            await pacer.drain_time(); ended.set()
                            await upstream.send(json.dumps({'end': True, 'sessionId': session_id}))

                async def receive():
                    received = 0
                    while True:
                        raw = await upstream.recv(); received += len(raw)
                        if received > 2*1024*1024: raise ValueError('语音服务返回数据过大。')
                        if not segments.update(raw): continue
                        value = segments.text() if segments.final else segments.preview()
                        for secret in credentials.values(): value = value.replace(secret, '[凭据已隐藏]')
                        if segments.final:
                            if not ended.is_set(): raise ValueError('转写提前结束，请重新录制。')
                            return value[:20000]
                        await browser.send_json({'type':'partial', 'text':value[:20000]})

                sender, receiver = asyncio.create_task(upload()), asyncio.create_task(receive())
                tasks = [sender, receiver]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if sender in done: await sender
                transcript = await receiver
                auth.current(browser)
                await browser.send_json({'type':'final','text':transcript,'seconds':stats['seconds']})
                stats['status'] = 'complete'
            finally:
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except (WebSocketDisconnect, asyncio.CancelledError): pass
    except Exception as exc:
        stats['status'] = 'error'
        message = str(exc) if type(exc) is ValueError or isinstance(exc, xfyun_asr.ASRError) else '语音连接中断或超时，请重试。'
        with suppress(Exception): await browser.send_json({'type':'error','detail':message})
    finally:
        if call_id: db.execute('UPDATE voice_calls SET seconds=?,status=? WHERE id=?', (stats['seconds'],stats['status'],call_id))
        if claimed: active.discard(uid)
        with suppress(Exception): await browser.close()
