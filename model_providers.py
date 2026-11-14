"""Multi-provider text inference with safe automatic failover.

Only preconfigured official HTTPS endpoints receive their matching key.
Model switching is for one text-generation call; the existing agent loop owns
all local tools, memory, and trace events.
"""
from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from typing import Callable

DEFAULT_ORDER = 'gemini,deepseek,doubao,qwen'


class AllProvidersFailed(RuntimeError):
    """Exhausted all configured model providers. Contains no keys/payloads."""


@dataclass(frozen=True)
class Provider:
    name: str
    key_env: str
    model_env: str
    default_model: str
    base_url: str | None = None


PROVIDERS = {
    'gemini': Provider('Gemini', 'GEMINI_API_KEY', 'GEMINI_MODEL', 'gemini-3.5-flash-lite'),
    'deepseek': Provider('DeepSeek', 'DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL', 'deepseek-flash', 'https://api.deepseek.com'),
    'doubao': Provider('豆包', 'ARK_API_KEY', 'DOUBAO_MODEL', 'doubao-seed-2-1-pro-260915', 'https://ark.cn-beijing.volces.com/api/v3'),
    'qwen': Provider('通义千问', 'DASHSCOPE_API_KEY', 'QWEN_MODEL', 'qwen-plus', 'https://dashscope.aliyuncs.com/compatible-mode/v1'),
}


def _error_brief(exc: Exception) -> str:
    """Never print provider exception bodies, which might include request data."""
    code = getattr(exc, 'status_code', None)
    if code is None:
        code = getattr(exc, 'code', None)
    status = str(code) if code is not None and str(code).isdigit() else ''
    return f'{type(exc).__name__} {status}'.strip()


def _call_gemini(provider: Provider, key: str, prompt: str) -> str:
    from google import genai

    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=os.getenv(provider.model_env, provider.default_model),
        contents=prompt,
    )
    return (response.text or '').strip()


def _call_openai_compatible(provider: Provider, key: str, prompt: str) -> str:
    from openai import OpenAI

    # Pay-as-you-go Qwen keys and base URLs must belong to the same region.
    # The optional override is used ONLY for Qwen; other hosts remain fixed.
    base_url = provider.base_url
    if provider.key_env == 'DASHSCOPE_API_KEY':
        base_url = os.getenv('QWEN_BASE_URL', base_url)
        if not base_url.startswith('https://') or not (
            base_url == 'https://dashscope.aliyuncs.com/compatible-mode/v1'
            or base_url == 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'
            or base_url == 'https://dashscope-us.aliyuncs.com/compatible-mode/v1'
            or base_url == 'https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1'
            or (base_url.endswith('/compatible-mode/v1') and base_url.split('/')[2].endswith('.maas.aliyuncs.com'))
        ):
            raise ValueError('QWEN_BASE_URL 必须是受支持的阿里云 HTTPS OpenAI 兼容端点')
    with OpenAI(api_key=key, base_url=base_url, timeout=25.0, max_retries=0) as client:
        result = client.chat.completions.create(
            model=os.getenv(provider.model_env, provider.default_model),
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.2,
        )
    if not result.choices:
        return ''
    content = result.choices[0].message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') if isinstance(part, dict) else getattr(part, 'text', '') for part in content).strip()
    return ''


def model_text_with_fallback(prompt: str, status: Callable[[str], None] = print, *, require_json: bool = False) -> str:
    order = [name.strip().lower() for name in os.getenv('MODEL_PROVIDER_ORDER', DEFAULT_ORDER).split(',') if name.strip()]
    if not order or any(name not in PROVIDERS for name in order):
        raise AllProvidersFailed('MODEL_PROVIDER_ORDER 含有不支持的服务名；支持 gemini,deepseek,doubao,qwen。')

    tried = []
    used = set()
    for name in order:
        if name in used:
            continue
        used.add(name)
        provider = PROVIDERS[name]
        key = os.getenv(provider.key_env, '').strip()
        if not key or key == 'YOUR_API_KEY_HERE':
            continue
        try:
            text = (_call_gemini(provider, key, prompt) if name == 'gemini'
                    else _call_openai_compatible(provider, key, prompt))
            if not text:
                raise ValueError('EmptyModelResponse')
            if require_json:
                cleaned = re.sub(r'^```(?:json)?\s*', '', text.strip())
                cleaned = re.sub(r'\s*```$', '', cleaned)
                parsed = json.loads(cleaned)
                if not isinstance(parsed, dict):
                    raise ValueError('Model response must be a JSON object')
            status(f'[模型线路] {provider.name} 调用成功')
            return text
        except Exception as exc:
            brief = _error_brief(exc)
            tried.append(f'{provider.name} ({brief})')
            status(f'[模型线路] {provider.name} 失败：{brief}；尝试下一条已配置线路')

    if not tried:
        raise AllProvidersFailed('没有可用的模型 API Key。请在 .env 中配置至少一个服务商的密钥。')
    raise AllProvidersFailed('全部模型线路失败：' + '、'.join(tried) + '。请检查各平台状态、模型 ID 与 API 配额。')
