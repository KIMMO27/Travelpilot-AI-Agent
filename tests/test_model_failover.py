"""No-network regression tests for automatic provider failover."""
import os
import unittest
from unittest.mock import patch

from model_providers import AllProvidersFailed, model_text_with_fallback


class Fake503(Exception):
    code = 503


class ModelFailoverTests(unittest.TestCase):
    def test_gemini_503_switches_to_deepseek(self):
        env = {
            'MODEL_PROVIDER_ORDER': 'gemini,deepseek,doubao,qwen',
            'GEMINI_API_KEY': 'gemini-test-key',
            'DEEPSEEK_API_KEY': 'deepseek-test-key',
        }
        with patch.dict(os.environ, env, clear=True):
            with patch('model_providers._call_gemini', side_effect=Fake503('private error')) as gm:
                with patch('model_providers._call_openai_compatible', return_value='{"action":"final","answer":"成功"}') as ds:
                    lines = []
                    result = model_text_with_fallback('hi', lines.append)
        self.assertIn('成功', result)
        self.assertEqual(gm.call_count, 1)
        self.assertEqual(ds.call_count, 1)
        self.assertEqual(ds.call_args.args[0].name, 'DeepSeek')
        self.assertTrue(any('503' in line for line in lines))
        self.assertFalse(any('private error' in line for line in lines))

    def test_missing_keys_are_skipped(self):
        with patch.dict(os.environ, {'MODEL_PROVIDER_ORDER': 'gemini,doubao,qwen', 'ARK_API_KEY': 'test'}, clear=True):
            with patch('model_providers._call_openai_compatible', return_value='hi') as call:
                result = model_text_with_fallback('hi', lambda _: None)
        self.assertEqual(result, 'hi')
        self.assertEqual(call.call_args.args[0].name, '豆包')

    def test_all_providers_failed_is_safe(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-secret'}, clear=True):
            with patch('model_providers._call_gemini', side_effect=Fake503('test-secret')):
                with self.assertRaises(AllProvidersFailed) as cm:
                    model_text_with_fallback('Hi', lambda _: None)
        self.assertIn('503', str(cm.exception))
        self.assertNotIn('test-secret', str(cm.exception))

    def test_no_configured_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AllProvidersFailed):
                model_text_with_fallback('hi', lambda _: None)

    def test_bad_provider_name_is_rejected(self):
        with patch.dict(os.environ, {'MODEL_PROVIDER_ORDER': 'gemini,example'}, clear=True):
            with self.assertRaises(AllProvidersFailed):
                model_text_with_fallback('hi', lambda _: None)

    def test_openai_models_are_read_from_env(self):
        # Provider configuration is centrally defined; no hardcoded customer credentials.
        from model_providers import PROVIDERS
        self.assertEqual(PROVIDERS['deepseek'].model_env, 'DEEPSEEK_MODEL')
        self.assertEqual(PROVIDERS['doubao'].key_env, 'ARK_API_KEY')
        self.assertEqual(PROVIDERS['qwen'].key_env, 'DASHSCOPE_API_KEY')


if __name__ == '__main__':
    unittest.main()

class EndToEndModelFallbackTests(unittest.TestCase):
    def test_travel_agent_continue_after_gemini_503(self):
        import tempfile
        from pathlib import Path
        from travel_agent.cli import respond
        import common
        import travel_agent.trace as trace
        with tempfile.TemporaryDirectory() as td:
            with patch.object(common, 'BASE', Path(td)), patch.object(trace, 'BASE', Path(td)):
                with patch.dict(os.environ, {'MODEL_PROVIDER_ORDER': 'gemini,deepseek',
                                            'GEMINI_API_KEY': 'mock-google',
                                            'DEEPSEEK_API_KEY': 'mock-deepseek'}, clear=True):
                    answers = iter([
                        '{"action":"tool","name":"search_activities","arguments":{"city":"墨尔本","interest":"咖啡"}}',
                        '{"action":"final","answer":"演示行程：第二天上午体验咖啡文化。"}',
                    ])
                    with patch('model_providers._call_gemini', side_effect=Fake503('server is overloaded')):
                        with patch('model_providers._call_openai_compatible', side_effect=lambda *args: next(answers)) as ds:
                            result = respond('墨尔本三天行程，喜欢咖啡', 'trip1', 'alice')
        self.assertIn('第二天上午', result)
        self.assertEqual(ds.call_count, 2)

    def test_invalid_json_result_uses_backup(self):
        with patch.dict(os.environ, {'MODEL_PROVIDER_ORDER': 'gemini,deepseek',
                                    'GEMINI_API_KEY': 'mock-google',
                                    'DEEPSEEK_API_KEY': 'mock-deepseek'}, clear=True):
            with patch('model_providers._call_gemini', return_value='not JSON'):
                with patch('model_providers._call_openai_compatible', return_value='{"action":"final","answer":"ok"}'):
                    output = model_text_with_fallback('prompt', lambda _: None, require_json=True)
        self.assertIn('"action"', output)
