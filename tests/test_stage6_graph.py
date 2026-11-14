"""Offline LangGraph regression tests: no API calls or user secrets."""
import os
import unittest
from unittest.mock import patch

from travel_agent import graph_flow as g


def sample_plan():
    result = {'itinerary': [], 'budget_note': '缺少实时价格，未验证预算', 'issues': [], 'status': 'complete_candidates'}
    for day in range(1, 4):
        result['itinerary'].append({'day': day, 'slots': [
            {'slot': s, 'status': 'place_found', 'name': f'{s}咖啡馆{day}',
             'osm_url': f'https://www.openstreetmap.org/node/{100*day+i}'}
            for i, s in enumerate(['上午', '下午', '晚上'])],
            'route': {'status': 'ok', 'source': 'Geoapify Routing API', 'distance_km': 1.5,
                      'estimated_minutes': 23}})
    return result


class Stage6GraphTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'TRAVEL_DATA_MODE': 'osm', 'TRAVEL_MAP_PROVIDER': 'geoapify'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.mem = patch.object(g, 'remember_explicit_preferences', return_value={'likes': ['咖啡'], 'dislikes': []})
        self.mem.start()
        self.addCleanup(self.mem.stop)
        self.logs = patch.object(g, 'record_event')
        self.logs.start()
        self.addCleanup(self.logs.stop)

    def base(self, msg='帮我规划墨尔本3天旅行，每天预算150澳元，我喜欢咖啡'):
        return {'user_id': 'demo_user', 'session_id': 'stage6test', 'run_id': 'offline', 'message': msg}

    def test_graph_success_and_grounded_route(self):
        with patch.object(g, 'run_tool', return_value=sample_plan()) as tool, \
             patch.object(g, 'model_text_with_fallback', return_value='很高兴为你规划旅行。'):
            output = g.build_graph().invoke(self.base())
        self.assertEqual(output['outcome'], 'success')
        self.assertIn('https://www.openstreetmap.org/node/100', output['answer'])
        self.assertIn('1.5公里', output['answer'])
        self.assertIn('23分钟', output['answer'])
        self.assertEqual(tool.call_args.args[0], 'plan_trip')

    def test_unknown_city_stops_before_tool(self):
        with patch.object(g, 'run_tool') as tool:
            output = g.build_graph().invoke(self.base('帮我规划东京3天旅行'))
        self.assertEqual(output['outcome'], 'failed')
        tool.assert_not_called()

    def test_missing_route_never_invents_times(self):
        data = sample_plan()
        data['itinerary'][0]['route'] = {'status': 'unavailable'}
        with patch.object(g, 'run_tool', return_value=data), \
             patch.object(g, 'model_text_with_fallback', side_effect=RuntimeError('offline')):
            output = g.build_graph().invoke(self.base())
        self.assertIn('地图服务未提供可验证的步行距离和时间', output['answer'])
        self.assertEqual(output['answer'].count('23分钟'), 2)

    def test_tool_error_safe_branch(self):
        with patch.object(g, 'run_tool', return_value={'error': '服务暂时不可用'}), \
             patch.object(g, 'model_text_with_fallback') as model:
            output = g.build_graph().invoke(self.base())
        self.assertEqual(output['outcome'], 'failed')
        self.assertIn('服务暂时不可用', output['answer'])
        model.assert_not_called()

    def test_bad_osm_urls_do_not_pass(self):
        data = sample_plan()
        for d in data['itinerary']:
            for s in d['slots']:
                s['osm_url'] = 'https://example.com/fake'
        with patch.object(g, 'run_tool', return_value=data):
            output = g.build_graph().invoke(self.base())
        self.assertEqual(output['outcome'], 'failed')
        self.assertNotIn('example.com', output['answer'])


if __name__ == '__main__':
    unittest.main()
