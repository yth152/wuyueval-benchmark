"""Model catalog; public metadata never includes endpoints or credentials."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
import store as db


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,47}$')
    display_name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=160)
    context_window: int = Field(default=32768, ge=8192, le=262144)
    protocol: Literal['team', 'baseline'] = 'baseline'
    enabled: bool = True
    supports_vision: bool = False
    supports_tools: bool = True
    thinking_mode: Literal['auto', 'chat_template', 'enable_thinking', 'both', 'none'] = 'auto'
    max_output_tokens: int | None = Field(default=None, ge=512, le=16384)
    temperature: float | None = Field(default=None, ge=0, le=1.5)
    system_prompt: str = Field(default='', max_length=8000)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''), max_length=4096)

    @model_validator(mode='after')
    def validate_profile(self):
        if self.id == 'team': raise ValueError('team 为默认模型保留标识。')
        url = urlsplit(self.base_url)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('模型 Base URL 无效。')
        self.base_url = self.base_url.rstrip('/')
        if not self.display_name.strip() or not self.model.strip(): raise ValueError('模型名称不能为空。')
        return self


def secret_name(model_id):
    return 'model' if model_id == 'team' else 'provider_' + model_id


def catalog(cfg):
    primary = {k: cfg[k] for k in ('display_name', 'base_url', 'model', 'context_window')}
    primary.update(supports_vision=cfg.get('supports_vision', True), supports_tools=cfg.get('supports_tools', True),
                   thinking_mode=cfg.get('thinking_mode', 'chat_template'))
    others = [ModelProfile(**p).model_dump(exclude={'api_key'}) for p in cfg['additional_models']]
    return [dict(primary, id='team', protocol='team', enabled=True), *others]


def thinking_mode(profile):
    mode = profile.get('thinking_mode', 'auto')
    return ('both' if profile['protocol'] == 'baseline' else 'chat_template') if mode == 'auto' else mode


def thinking_parameters(mode, enabled):
    result = {}
    if mode in ('chat_template', 'both'): result['chat_template_kwargs'] = {'enable_thinking': enabled}
    if mode in ('enable_thinking', 'both'): result['enable_thinking'] = enabled
    return result


def resolve(cfg, model_id):
    return next((p for p in catalog(cfg) if p['id'] == model_id and p['enabled']), None)


def public_catalog(cfg):
    return [{'id': p['id'], 'display_name': p['display_name'], 'ready': bool(db.secret(secret_name(p['id']))),
             'supports_vision': p.get('supports_vision', False), 'supports_tools': p.get('supports_tools', True),
             'supports_thinking': thinking_mode(p) != 'none',
             'max_output_tokens': p.get('max_output_tokens') or cfg['max_output_tokens']}
            for p in catalog(cfg) if p['enabled']]
