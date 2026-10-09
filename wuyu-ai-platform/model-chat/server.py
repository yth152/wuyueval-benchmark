import asyncio
import json
import time
import uuid
from datetime import datetime
from contextlib import aclosing
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, PrivateAttr, model_validator
import attachments

from credentials import PROVIDERS, api_key, configured
from agent_stream import run_agent
from search_tools import search_configured

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="无隅 · 模型对话", docs_url=None, redoc_url=None, openapi_url=None)
active = {name: 0 for name in PROVIDERS}


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.headers.get("host", "")
    if host not in {"127.0.0.1:8910", "localhost:8910"}:
        return JSONResponse({"detail": "仅允许本机访问。"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != "http://" + host:
        return JSONResponse({"detail": "不允许跨站访问。"}, status_code=403)
    upload = request.url.path == '/api/files' and request.method == 'POST'
    if upload:
        try: length = int(request.headers.get('content-length', '0'))
        except ValueError: length = 0
        if not 0 < length <= (attachments.FILE_MB * 1024 * 1024 + 65536):
            return JSONResponse({'detail':'上传文件必须不超过 20 MB。'}, status_code=413)
    if request.method == "POST" and not upload and not request.headers.get("content-type", "").startswith("application/json"):
        return JSONResponse({"detail": "请求必须使用 application/json。"}, status_code=415)
    response = await call_next(request)
    response.headers.update({
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    })
    return response


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)
    file_ids: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def validate_files(self):
        import re
        if self.role != 'user' and self.file_ids: raise ValueError('只能给用户问题附加文件。')
        if len(set(self.file_ids)) != len(self.file_ids) or any(not re.fullmatch(r'[a-f0-9]{32}', f) for f in self.file_ids):
            raise ValueError('附件编号无效或重复。')
        return self


class ChatRequest(BaseModel):
    _prepared_messages: list | None = PrivateAttr(default=None)
    _attachment_notices: list = PrivateAttr(default_factory=list)
    provider: Literal["team", "baseline"] = "team"
    messages: list[Message] = Field(min_length=1, max_length=41)
    system: str = Field(default="你是一个严谨、清晰的中文助手。请根据用户问题作答；信息不足时明确说明，不编造事实、数据、来源或法律条文。", max_length=8000)
    thinking: bool = True
    network_search: bool = False
    temperature: float = Field(default=0.2, ge=0, le=1.5)
    max_tokens: int = Field(default=8192, ge=512, le=16384)

    @model_validator(mode="after")
    def validate_context(self):
        if len(self.messages) % 2 != 1 or any(
                message.role != ("user" if index % 2 == 0 else "assistant")
                for index, message in enumerate(self.messages)):
            raise ValueError("历史消息应由完整的用户/助手对话组成，最后一条必须是用户问题。")
        if sum(len(message.content) for message in self.messages) + len(self.system) > 60000:
            raise ValueError("对话上下文过长，请新建对话或减少历史轮数。")
        return self


@app.get("/api/health")
async def health():
    return {"app": "wuyu-model-chat", "version": "1.4", "status": "ok"}


@app.get('/api/files/config')
async def file_config():
    return attachments.config()


@app.post('/api/files')
async def upload_file(file: UploadFile = File(...)):
    try:
        data=await file.read(attachments.FILE_MB*1024*1024+1)
        return await attachments.save_file(file.filename or '附件',data)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    finally: await file.close()


@app.get('/api/files/{file_id}/preview')
async def image_preview(file_id: str):
    try: item=attachments.read_file(file_id)
    except ValueError: raise HTTPException(404) from None
    path=attachments.UPLOAD_DIR/(file_id+'.jpg')
    if item['kind']!='image' or not path.is_file():raise HTTPException(404)
    return FileResponse(path,media_type='image/jpeg')


@app.get("/api/tools")
async def tool_status():
    return {"configured": search_configured(), "tools": ["web_search", "scholar_search"], "results_per_search": 10}


@app.get("/api/providers")
async def providers():
    return [{"id": name, "label": value["label"], "model": value["model"],
             "configured": configured(name)} for name, value in PROVIDERS.items()]


class ExportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3000000)


@app.post("/api/export")
async def export_chat(body: ExportRequest):
    directory = ROOT / "output/exports"
    directory.mkdir(parents=True, exist_ok=True)
    name = "chat-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".md"
    target = directory / name
    target.write_text(body.text, encoding="utf-8-sig")
    return {"path": str(target), "url": "/api/exports/" + name}


@app.get("/api/exports/{name}")
async def exported_chat(name: str):
    import re
    if not re.fullmatch(r"chat-\d{8}-\d{6}-[0-9a-f]{8}\.md", name):
        raise HTTPException(404)
    target = ROOT / "output/exports" / name
    if not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, filename=name, media_type="text/markdown; charset=utf-8")


async def stream_reply(body: ChatRequest, key: str):
    try:
        async with aclosing(run_agent(body, key)) as stream:
            async for chunk in stream:
                yield chunk
    finally:
        active[body.provider] -= 1


@app.post("/api/chat")
async def chat(body: ChatRequest):
    if active[body.provider] >= 2:
        raise HTTPException(429, "该模型已有两个请求正在生成，请等待或停止后再试。")
    try:
        key = api_key(body.provider)
    except (ValueError, OSError, KeyError):
        raise HTTPException(503, "本机模型凭据不可用，请检查 runtime 中的加密凭据。") from None
    if body.network_search and not search_configured():
        raise HTTPException(503, "搜索服务凭据不可用。请检查配置，或关闭联网搜索。")
    try:
        body._prepared_messages, body._attachment_notices = await asyncio.to_thread(attachments.prepare_messages,body)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    active[body.provider] += 1
    return StreamingResponse(stream_reply(body, key), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no"})


@app.get("/")
async def index():
    return FileResponse(ROOT / "dist/index.html")


@app.get("/favicon.svg")
async def icon():
    return FileResponse(ROOT / "dist/favicon.svg", media_type="image/svg+xml")


if (ROOT / "dist/assets").exists():
    app.mount("/assets", StaticFiles(directory=ROOT / "dist/assets"), name="assets")
