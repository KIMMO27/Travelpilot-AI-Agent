"""Fixed-path local skill. Only project-maintained files should be loaded."""
from pathlib import Path

SKILL_FILE = Path(__file__).resolve().parent / 'skills' / 'answer_quality' / 'SKILL.md'


def load_answer_skill():
    return SKILL_FILE.read_text(encoding='utf-8')[:4000]
