"""Unit tests requiring no network, API keys or model calls."""
import os
import unittest
import json
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from travel_agent.itinerary import plan_trip, walking_route
from travel_agent.tools import run_tool


def sample(category, count=6):
    rows = []
    for i in range(count):
        rows.append({
            'name': f'{category}-{i}', 'latitude': -37.813 + i*0.0004,
            'longitude': 144.963 + i*0.0004,
            'osm_url': f'https://www.openstreetmap.org/node/{i+1+(list(("cafe", "attraction", "park", "restaurant")).index(category)*100)}',
            'address': f'{category} example address',
        })
    return {'activities': rows, 'data_type': 'osm', 'data_source': 'Geoapify Places API', 'fetch_mode': 'network'}


def fake_search(city, tool, interest):
    mapping = {'咖啡': 'cafe', '博物馆': 'attraction', '公园': 'park', '餐厅': 'restaurant'}
    return sample(mapping[interest])


class Stage5Tests(unittest.TestCase):
    def test_input_rejects_bad_days(self):
        self.assertIn('error', plan_trip({'city': '墨尔本', 'days': 999}))
        self.assertIn('error', plan_trip({'city': '墨尔本', 'days': True}))
        self.assertIn('error', plan_trip({'city': '伦敦', 'days': 3}))

    @patch.dict(os.environ, {'TRAVEL_DATA_MODE': 'osm', 'TRAVEL_MAP_PROVIDER': 'geoapify'})
    @patch('travel_agent.itinerary.walking_route', return_value={'status': 'unavailable', 'reason': 'test'})
    @patch('travel_agent.itinerary.search_places', side_effect=fake_search)
    def test_three_days_real_names_without_fabricated_times(self, search, route):
        result = run_tool('plan_trip', {'city': '墨尔本', 'days': 3, 'interest': '咖啡', 'daily_budget_aud': 120})
        self.assertEqual(result['status'], 'complete_candidates')
        self.assertEqual(result['found_slots'], 9)
        self.assertEqual(result['itinerary'][1]['slots'][0]['category'], 'cafe')
        urls = [s['osm_url'] for d in result['itinerary'] for s in d['slots'] if s['status'] == 'place_found']
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(result['itinerary'][0]['route']['status'], 'unavailable')
        self.assertIn('未验证', result['budget_note'])
        self.assertEqual(search.call_count, 4)
        self.assertEqual(route.call_count, 3)

    @patch.dict(os.environ, {'TRAVEL_DATA_MODE': 'osm', 'TRAVEL_MAP_PROVIDER': 'geoapify'})
    @patch('travel_agent.itinerary.walking_route', return_value={'status': 'not_needed'})
    @patch('travel_agent.itinerary.search_places', side_effect=lambda city, tool, interest: {'error': 'unavailable'})
    def test_no_fake_places(self, *_):
        result = run_tool('plan_trip', {'city': '墨尔本', 'days': 3})
        self.assertEqual(result['status'], 'partial_candidates')
        self.assertEqual(result['found_slots'], 0)
        self.assertTrue(all(s['status'] == 'unavailable' for d in result['itinerary'] for s in d['slots']))

    @patch('travel_agent.itinerary._geoapify_key', return_value='demo_key')
    def test_routing_success_uses_geoapify_metrics(self, *_):
        import travel_agent.itinerary as mod
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, n):
                return json.dumps({'features':[{'properties': {'distance': 1800, 'time': 1320,
                    'legs': [{'distance': 1800, 'time': 1320}]}}]}).encode()
        points = [{'name': 'A', 'latitude': -37.813, 'longitude': 144.963},
                  {'name': 'B', 'latitude': -37.814, 'longitude': 144.964}]
        with TemporaryDirectory() as tmp, patch.object(mod, 'ROUTE_DIR', Path(tmp)):
            with patch('travel_agent.itinerary.urlopen', return_value=FakeResponse()) as http:
                answer = walking_route(points)
                self.assertEqual(answer['status'], 'ok')
                self.assertEqual(answer['estimated_minutes'], 22)
                self.assertEqual(answer['distance_km'], 1.8)
                request = http.call_args.args[0]
                self.assertIn('mode=walk', request.full_url)
                self.assertNotIn('demo_key', json.dumps(answer))

    @patch('travel_agent.itinerary._geoapify_key', return_value='')
    def test_route_never_invents_metrics_if_missing_key(self, *_):
        stops = [{'name': 'A', 'latitude': -37.8, 'longitude': 144.9},
                 {'name': 'B', 'latitude': -37.81, 'longitude': 144.91}]
        result = walking_route(stops)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('estimated_minutes', result)


if __name__ == '__main__':
    unittest.main()
