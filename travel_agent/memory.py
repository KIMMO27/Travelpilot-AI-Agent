"""Local-only travel profile. Not authentication or a production memory backend."""
import json
import re

from common import session_file

# A deliberately small allowlist of non-sensitive travel interests.
INTERESTS = ('咖啡', '艺术', '博物馆', '自然', '徒步', '美食', '购物', '海滩')


def load_profile(user_id='demo_user'):
    path = session_file('travel_profiles', user_id)
    if not path.exists():
        return {'likes': [], 'dislikes': []}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {'likes': [], 'dislikes': []}
    if not isinstance(data, dict):
        return {'likes': [], 'dislikes': []}
    return {
        'likes': [x for x in data.get('likes', []) if x in INTERESTS],
        'dislikes': [x for x in data.get('dislikes', []) if x in INTERESTS],
    }


def save_profile(user_id, profile):
    path = session_file('travel_profiles', user_id)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def remember_explicit_preferences(user_id, text):
    """Store only explicitly worded likes/dislikes; no LLM inference, no secret extraction."""
    profile = load_profile(user_id)
    for item in INTERESTS:
        # Prefer the negative pattern. Search a short clause to avoid joining unrelated sentences.
        dislike = bool(re.search(r'(?:不喜欢|不太喜欢|讨厌)\s*' + re.escape(item), text))
        like = bool(re.search(r'(?<!不)(?<!不太)(?:喜欢|偏爱|偏好)\s*' + re.escape(item), text))
        if dislike:
            if item not in profile['dislikes']:
                profile['dislikes'].append(item)
            if item in profile['likes']:
                profile['likes'].remove(item)
        elif like:
            if item not in profile['likes']:
                profile['likes'].append(item)
            if item in profile['dislikes']:
                profile['dislikes'].remove(item)
    save_profile(user_id, profile)
    return profile


def forget_profile(user_id):
    path = session_file('travel_profiles', user_id)
    path.unlink(missing_ok=True)
