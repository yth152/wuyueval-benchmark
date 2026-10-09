"""Local credentials. Plaintext keys are never sent to the browser or logged."""
import base64
import ctypes
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
PROVIDERS = {
    "team": {
        "label": "wuyu-shensi", "model": "qwen3.8-27b-tmidtrain",
        "base_url": "http://103.139.212.177:8443/v1", "env": "TEAM_API_KEY",
        "tokenize_url": "http://103.139.212.177:8443/tokenize", "context_window": 32768,
    },
    "baseline": {
        "label": "在线 Qwen 基线", "model": "qwen3.8-27b",
        "base_url": "https://www.dmxapi.cn/v1", "env": "BASELINE_API_KEY",
    },
}


def dpapi(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise ValueError("本机加密凭据仅支持 Windows，请通过环境变量配置密钥。")
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    function = (ctypes.windll.crypt32.CryptUnprotectData if decrypt
                else ctypes.windll.crypt32.CryptProtectData)
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError("本机凭据解密失败，请使用保存凭据的 Windows 用户启动。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(result.data)


def seal(key: str) -> str:
    return "dpapi:" + base64.b64encode(dpapi(key.encode())).decode()


def unseal(value: str) -> str:
    if not value.startswith("dpapi:"):
        raise ValueError("凭据格式无效。")
    return dpapi(base64.b64decode(value[6:]), True).decode()


def api_key(provider: str) -> str:
    if key := os.environ.get(PROVIDERS[provider]["env"]):
        return key
    saved = RUNTIME / (provider + ".dpapi")
    if saved.exists():
        return unseal(saved.read_text(encoding="utf-8"))
    if provider == "baseline":
        database = ROOT.parents[1] / "solid-waste-distiller/data/runtime/studio.sqlite3"
        if database.exists():
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                for (body,) in db.execute("SELECT body FROM docs WHERE kind='provider'"):
                    item = json.loads(body)
                    if (item.get("id") == "api_3e52836d57ee429c" and item.get("enabled")
                            and item.get("base_url", "").rstrip("/") == PROVIDERS[provider]["base_url"]):
                        return unseal(item["secret"])
    raise ValueError("该模型尚未配置本机 API 密钥。")


def configured(provider: str) -> bool:
    try:
        return bool(api_key(provider))
    except (ValueError, OSError, sqlite3.Error, KeyError):
        return False


def metaso_key() -> str:
    if key := os.environ.get("METASO_API_KEY"):
        return key
    saved = RUNTIME / "metaso.dpapi"
    if saved.exists():
        return unseal(saved.read_text(encoding="utf-8"))
    directory = ROOT.parent / "runtime"
    database = directory / "platform.sqlite3"
    if database.exists() and (directory / "vault.key").exists():
        from cryptography.fernet import Fernet
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT value FROM settings WHERE key='metaso_secret'").fetchone()
        if row and row[0]:
            return Fernet((directory / "vault.key").read_bytes()).decrypt(row[0].encode()).decode()
    raise ValueError("尚未配置秘塔搜索密钥。")

