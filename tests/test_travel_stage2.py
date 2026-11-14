"""Offline tests for the added travel functions. No network or Gemini key required."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from travel_agent.cli import _history_key, respond
from travel_agent.memory import load_profile, remember_explicit_preferences
from travel_agent.skills import load_answer_skill
from travel_agent.trace import record_event


class Stage2TravelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base_patch = patch('common.BASE', self.root)
        self.trace_patch = patch('travel_agent.trace.BASE', self.root)
        self.base_patch.start()
        self.trace_patch.start()

    def tearDown(self):
        self.trace_patch.stop()
        self.base_patch.stop()
        self.temp.cleanup()

    def test_memory_is_per_user_and_explicit(self):
        remember_explicit_preferences('alice', '我喜欢咖啡')
        self.assertEqual(load_profile('alice')['likes'], ['咖啡'])
        self.assertEqual(load_profile('bob')['likes'], [])
        remember_explicit_preferences('alice', '我不喜欢咖啡')
        self.assertEqual(load_profile('alice')['likes'], [])
        self.assertEqual(load_profile('alice')['dislikes'], ['咖啡'])

    def test_session_key_is_per_user_and_session(self):
        self.assertNotEqual(_history_key('alice', 'one'), _history_key('bob', 'one'))
        self.assertNotEqual(_history_key('alice', 'one'), _history_key('alice', 'two'))
        with self.assertRaises(ValueError):
            _history_key('../alice', 'one')

    def test_skill_is_loadable(self):
        self.assertIn('旅游推荐回答质量规范', load_answer_skill())

    def test_trace_has_no_raw_payload(self):
        p = record_event('alice', 'one', 'run01', 'tool_execute', tool='search_hotels', status='ok', secret='SENSITIVE')
        row = json.loads(p.read_text(encoding='utf-8').splitlines()[0])
        self.assertEqual(row['tool'], 'search_hotels')
        self.assertNotIn('secret', row)

    def test_agent_with_mock_model_and_mock_tool(self):
        actions = iter([
            '{"action":"tool","name":"search_hotels","arguments":{"city":"墨尔本"}}',
            '{"action":"final","answer":"这是虚构演示酒店，请不要视为实时可订。"}',
        ])
        with patch('travel_agent.cli.model_text', side_effect=lambda prompt: next(actions)) as m:
            result = respond('我喜欢咖啡，帮我找墨尔本酒店', 'test01', 'alice')
        self.assertIn('虚构演示', result)
        self.assertEqual(m.call_count, 2)
        self.assertIn('answer-quality', m.call_args_list[0].args[0])
        self.assertIn('咖啡', load_profile('alice')['likes'])
        trace_dir = self.root / 'local_traces' / 'travel' / 'alice'
        self.assertEqual(len(list(trace_dir.glob('*.jsonl'))), 1)
        text = next(trace_dir.glob('*.jsonl')).read_text()
        self.assertIn('tool_execute', text)
        self.assertNotIn('我喜欢咖啡', text)


if __name__ == '__main__':
    unittest.main()
