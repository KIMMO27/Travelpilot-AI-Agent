"""Shared multi-provider model and local-session utilities for both projects."""
import json
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent


def model_text(prompt):
    """Generate with Gemini, DeepSeek, Doubao or Qwen; fall back on API errors."""
    from dotenv import load_dotenv
    from model_providers import model_text_with_fallback
    load_dotenv(BASE / '.env')
    return model_text_with_fallback(prompt, require_json=('只输出一种 JSON 对象' in prompt))


def json_from_model(raw):
    """Parse one model-produced JSON object, without executing anything."""
    raw = raw.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError('模型必须返回 JSON 对象')
    return obj


def session_file(project, session_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', session_id):
        raise ValueError('session_id 只能包含字母、数字、_ 或 -，最长 40 位。')
    folder = BASE / 'local_sessions' / project
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{session_id}.json'


def load_history(project, session_id):
    path = session_file(project, session_id)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding='utf-8'))
    return data if isinstance(data, list) else []


def save_history(project, session_id, history):
    path = session_file(project, session_id)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(history[-30:], ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
