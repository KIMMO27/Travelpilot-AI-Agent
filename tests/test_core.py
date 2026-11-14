import unittest

from common import json_from_model, session_file
from travel_agent.tools import run_tool
from ecommerce_agent.retrieval import bm25_rank, rrf_fuse, tokenize
from ecommerce_agent.cli import route_intent


class PortfolioTests(unittest.TestCase):
    def test_tool_allowlist(self):
        self.assertIn('error', run_tool('delete_orders', {'user_id': 'other'}))

    def test_mock_hotel_data(self):
        result = run_tool('search_hotels', {'city': '墨尔本', 'max_price_aud': 120})
        self.assertEqual(result['data_type'], 'mock')
        self.assertEqual(len(result['hotels']), 1)

    def test_budget_validation(self):
        self.assertIn('error', run_tool('estimate_budget', {'days': -1}))
        result = run_tool('estimate_budget', {'days': 3, 'people': 2, 'daily_budget_aud': 100})
        self.assertEqual(result['estimated_total_aud'], 600.0)

    def test_parse_json_without_exec(self):
        self.assertEqual(json_from_model('```json\n{"action":"final","answer":"ok"}\n```')['answer'], 'ok')

    def test_reject_path_traversal(self):
        with self.assertRaises(ValueError):
            session_file('travel', '../other')

    def test_tokenize_chinese(self):
        self.assertIn('退货', tokenize('七天无理由退货'))

    def test_bm25(self):
        docs = ['咖啡机不出水如何检查', '退货运费谁承担', '空气炸锅怎么清洁']
        self.assertEqual(bm25_rank('退货运费', docs)[0], 1)

    def test_rrf(self):
        self.assertEqual(rrf_fuse([['B', 'A'], ['A', 'C']])[0], 'A')

    def test_router(self):
        self.assertEqual(route_intent('发票怎么开'), 'customer_service')
        self.assertEqual(route_intent('空气炸锅怎么清洁'), 'product_manual')
        self.assertEqual(route_intent('坏了怎么办'), 'clarify')


if __name__ == '__main__':
    unittest.main()
