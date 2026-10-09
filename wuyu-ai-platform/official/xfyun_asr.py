"""XFYun realtime large-model ASR wire adapter; credentials never enter logs."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import re
import time
import unicodedata
from urllib.parse import quote, urlencode
import uuid

from websockets.asyncio.client import connect

ENDPOINT = "wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1"
FRAME_BYTES = 1280
FRAME_SECONDS = .04
LOG = logging.Logger("wuyu.xfyun.private")
LOG.disabled = True  # WebSocket debug logging would expose the signed URL.


class ASRError(Exception):
    """Only safe, application-owned error messages are exposed to callers."""


class NoRedirectConnect(connect):
    def process_redirect(self, exc):
        return exc  # Never forward a credential-bearing URL to another endpoint.


class PCMSender:
    """Preserve realtime pacing even after a delayed network/browser batch."""
    def __init__(self, websocket):
        self.websocket = websocket
        self.next_at = None

    async def send(self, chunk):
        await self.drain_time()
        sent_at = time.monotonic()
        await self.websocket.send(chunk)
        self.next_at = sent_at + len(chunk) / 32000

    async def drain_time(self):
        if self.next_at is not None:
            wait = self.next_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)


def signed_url(app_id, api_key, api_secret, *, endpoint=ENDPOINT, utc=None, request_uuid=None):
    parameters = {"accessKeyId": api_key, "appId": app_id, "uuid": request_uuid or uuid.uuid4().hex,
                  "utc": utc or datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "audio_encode": "pcm_s16le", "lang": "autodialect", "samplerate": "16000"}
    canonical = "&".join(f"{quote(key, safe='')}={quote(str(value), safe='')}" for key, value in sorted(parameters.items()))
    parameters["signature"] = base64.b64encode(hmac.new(api_secret.encode(), canonical.encode(), hashlib.sha1).digest()).decode("ascii")
    return endpoint + "?" + urlencode(parameters)


def parse_message(raw):
    try:
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise ValueError()
        data = message.get("data", {})
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError()
        code = message.get("code", data.get("code"))
        if (message.get("action") == "error" or message.get("msg_type") == "error"
                or code not in (None, 0, "0") or data.get("normal") is False):
            suffix = str(code) if re.fullmatch(r"\d{1,8}", str(code or "")) else "未知"
            raise ASRError(f"讯飞转写返回错误（代码 {suffix}）；请核对服务权限和额度，本次未自动重试。")
        return message, data
    except ASRError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise ASRError("讯飞返回的数据格式无效，本次未自动重试。") from None


class Segments:
    def __init__(self):
        self.committed = []
        self.pending = None
        self.seen = set()
        self.final = False
        self.received = 0

    def preview(self):
        return ("".join(row["text"] for row in self.committed) + (self.pending["text"] if self.pending else "")).strip()

    def update(self, raw):
        self.received += len(raw)
        if self.received > 8 * 1024 * 1024:
            raise ASRError("讯飞返回数据超出大小限制。")
        message, data = parse_message(raw)
        if (message.get("action") == "result" or message.get("msg_type") == "result") and message.get("res_type", "asr") == "asr":
            self.accept(data)
            return True
        return False

    def accept(self, data):
        st = (data.get("cn") or {}).get("st")
        if st is not None:
            if not isinstance(st, dict):
                raise ASRError("讯飞转写片段格式无效。")
            number = data.get("seg_id")
            if isinstance(number, str) and number.isdecimal():
                number = int(number)
            if type(number) is not int or not 0 <= number <= 10000 or str(st.get("type")) not in {"0", "1"}:
                raise ASRError("讯飞转写片段编号或状态无效。")
            words = []
            try:
                for row in st.get("rt", []):
                    for word in row.get("ws", []):
                        candidates = word.get("cw", [])
                        # cw may contain alternatives: retain the first, not all candidates.
                        if candidates:
                            value = candidates[0].get("w", "")
                            if not isinstance(value, str):
                                raise ValueError()
                            words.append(value)
            except (TypeError, AttributeError, ValueError, IndexError):
                raise ASRError("讯飞转写词条格式无效。") from None
            value = "".join(words)
            final = str(st["type"]) == "0"

            def stamp(name):
                raw = st.get(name)
                if isinstance(raw, str) and raw.isdecimal():
                    raw = int(raw)
                return raw if type(raw) is int and raw >= 0 else None

            bg, ed = stamp("bg"), stamp("ed")
            identity = (number, final, bg, ed, value)
            if identity not in self.seen:
                self.seen.add(identity)
                if not final:
                    # seg_id is a MESSAGE number, not a stable sentence ID.
                    # Intermediate messages replace one current hypothesis;
                    # their bg/ed can move while the phrase grows.
                    covered = any((bg is not None and ed is not None and row["bg"] is not None
                                   and row["ed"] is not None and row["bg"] <= bg <= ed <= row["ed"])
                                  or (bg is None and number in row["message_ids"])
                                  for row in self.committed)
                    if not covered:
                        self.pending = {"text": value, "bg": bg, "ed": ed}
                else:
                    punctuation = bool(value.strip()) and all(c.isspace() or unicodedata.category(c).startswith("P") for c in value)
                    # A trailing period or empty terminal frame confirms no
                    # new words. Keep an outstanding next-phrase hypothesis
                    # so text() rejects an incomplete session instead of
                    # silently returning only earlier committed sentences.
                    if value.strip() and not punctuation:
                        pending = self.pending
                        covered = (not pending or bg is None or ed is None
                                   or pending["bg"] is None or pending["ed"] is None
                                   or bg <= pending["bg"] <= pending["ed"] <= ed)
                        if covered:
                            self.pending = None
                    match = next((row for row in reversed(self.committed)
                                  if (bg is not None and row["bg"] == bg and row["ed"] == ed)
                                  or (bg is None and number in row["message_ids"])), None)
                    if match is None and bg is not None:
                        match = next((row for row in reversed(self.committed) if row["bg"] == bg), None)
                    if punctuation:
                        # The real API may send a separate final punctuation
                        # message with exactly the previous segment's bg/ed.
                        target = match or (self.committed[-1] if self.committed else None)
                        if target:
                            if not target["text"].endswith(value):
                                target["text"] += value
                            target["message_ids"].add(number)
                    elif value:
                        if match:
                            match.update(text=value, bg=bg, ed=ed)
                            match["message_ids"].add(number)
                        else:
                            self.committed.append({"text": value, "bg": bg, "ed": ed, "message_ids": {number}})
                    # An empty terminal message never erases committed words.
            if len(self.seen) > 10000 or len(self.committed) > 3000 or len(self.preview()) > 30000:
                raise ASRError("讯飞转写文本超出长度限制。")
        if data.get("ls") is True:
            self.final = True

    def text(self):
        if not self.final or self.pending is not None:
            raise ASRError("讯飞未返回完整的最终转写结果，请核对调用记录后重试。")
        value = self.preview()
        if not value:
            raise ASRError("音频中没有识别到可用文字，请检查麦克风或音频内容。")
        return value


@asynccontextmanager
async def open_session(credentials, *, endpoint=None, start_timeout=15):
    url = signed_url(credentials["app_id"], credentials["api_key"], credentials["api_secret"], endpoint=endpoint or ENDPOINT)
    established = False
    try:
        async with NoRedirectConnect(url, open_timeout=start_timeout, close_timeout=2, proxy=None,
                                     max_size=262144, max_queue=8, compression=None, logger=LOG) as ws:
            message, data = parse_message(await asyncio.wait_for(ws.recv(), timeout=start_timeout))
            action = message.get("action") or data.get("action")
            session_id = data.get("sessionId") or message.get("sessionId") or message.get("sid")
            if not (action == "started" or message.get("msg_type") == "action") or not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
                raise ASRError("讯飞未返回有效的开始会话确认，本次未发送音频。")
            established = True
            yield ws, session_id
    except (ASRError, asyncio.CancelledError, TimeoutError):
        raise
    except Exception:
        if established:
            raise
        raise ASRError("讯飞语音连接中断或握手失败；请核对调用记录，本次未自动重试。") from None


async def transcribe_pcm(credentials, pcm, *, endpoint=None, start_timeout=15, finish_timeout=25, on_audio_sent=None):
    """Tests may inject a loopback endpoint; no HTTP configuration exposes this option."""
    destination = endpoint or ENDPOINT
    url = signed_url(credentials["app_id"], credentials["api_key"], credentials["api_secret"], endpoint=destination)
    duration = len(pcm) / 32000
    sender = None
    try:
        async with asyncio.timeout(duration + start_timeout + finish_timeout + 5):
            async with NoRedirectConnect(url, open_timeout=start_timeout, close_timeout=2, proxy=None,
                                         max_size=262144, max_queue=8, compression=None, logger=LOG) as ws:
                # Wait for the service's started/sessionId message before the first PCM frame.
                raw = await asyncio.wait_for(ws.recv(), timeout=start_timeout)
                message, data = parse_message(raw)
                action = message.get("action") or data.get("action")
                session_id = data.get("sessionId") or message.get("sessionId") or message.get("sid")
                if not (action == "started" or message.get("msg_type") == "action") or not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
                    raise ASRError("讯飞未返回有效的开始会话确认，本次未发送音频。")

                async def send_audio():
                    pacer = PCMSender(ws)
                    for offset in range(0, len(pcm), FRAME_BYTES):
                        await pacer.send(pcm[offset:offset + FRAME_BYTES])
                        if on_audio_sent:
                            on_audio_sent(min(offset + FRAME_BYTES, len(pcm)))
                    await pacer.drain_time()
                    await ws.send(json.dumps({"end": True, "sessionId": session_id}))

                sender = asyncio.create_task(send_audio())
                segments = Segments()
                received = 0
                # Sending and receiving run concurrently; the final frame is mandatory.
                async with asyncio.timeout(duration + finish_timeout):
                    while not segments.final:
                        raw = await ws.recv()
                        received += len(raw)
                        if received > 8 * 1024 * 1024:
                            raise ASRError("讯飞返回数据超出大小限制。")
                        message, data = parse_message(raw)
                        if message.get("action") == "result" or message.get("msg_type") == "result":
                            if message.get("res_type", "asr") == "asr":
                                segments.accept(data)
                    await sender
                return {"text": segments.text(), "audio_seconds": duration}
    except (ASRError, asyncio.CancelledError, TimeoutError):
        raise
    except Exception:
        # Library errors often contain a signed URL. Never propagate their string.
        raise ASRError("讯飞语音连接中断或握手失败；请核对调用记录，本次未自动重试。") from None
    finally:
        if sender is not None:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
