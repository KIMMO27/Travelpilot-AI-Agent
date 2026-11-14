"""Small local JSONL trace for one-process educational demos.

Do not store prompts, API keys, raw tool arguments, user text, or provider payloads.
"""
import json
from datetime import datetime, timezone
from uuid import uuid4

from common import BASE, session_file


def new_run_id():
    return uuid4().hex


def record_event(user_id, session_id, run_id, event, **safe_fields):
    # Validate IDs using the same strict path checks as chat history.
    session_file('travel_profiles', user_id)
    session_file('travel', session_id)
    directory = BASE / 'local_traces' / 'travel' / user_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{session_id}.jsonl'
    row = {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'run_id': run_id,
        'event': event,
    }
    # Explicit allowlist to prevent accidental sensitive logging.
    allowed = {'step', 'tool', 'status', 'duration_ms', 'action'}
    row.update({k: v for k, v in safe_fields.items() if k in allowed})
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    return path
