"""Offline, network-mocked tests for opt-in real-POI Overpass mode."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from travel_agent import osm_places
from travel_agent.tools import run_tool


class Response:
    def __init__(self, data):
        self.buf = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.buf.read(size)


class OSMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache_patch = patch.object(osm_places, 'CACHE_DIR', Path(self.temp.name))
        self.cache_patch.start()
        self.mode_patch = patch.dict(os.environ, {'TRAVEL_DATA_MODE': 'osm'})
        self.mode_patch.start()

    def tearDown(self):
        self.mode_patch.stop()
        self.cache_patch.stop()
        self.temp.cleanup()

    def test_cafe_from_openstreetmap_and_cache(self):
        content = {'elements': [
            {'type': 'node', 'id': 4242, 'lat': -37.813, 'lon': 144.966,
             'tags': {'name': 'Example Real Cafe'}},
            {'type': 'node', 'id': 4243, 'lat': -37.815, 'lon': 144.963,
             'tags': {}},
        ]}
        with patch.object(osm_places, 'urlopen', return_value=Response(json.dumps(content).encode())) as network:
            first = run_tool('search_activities', {'city': '墨尔本', 'interest': '咖啡'})
            second = run_tool('search_activities', {'city': '墨尔本', 'interest': '咖啡'})
        self.assertEqual(network.call_count, 1, 'cache prevents repeated network requests')
        self.assertEqual(first['fetch_mode'], 'network')
        self.assertEqual(second['fetch_mode'], 'cache')
        self.assertEqual(first['data_type'], 'osm')
        self.assertEqual(first['activities'][0]['name'], 'Example Real Cafe')
        self.assertEqual(first['activities'][0]['osm_url'], 'https://www.openstreetmap.org/node/4242')
        self.assertNotIn('nightly_aud', first['activities'][0])
        self.assertIn('营业时间', first['not_verified'])
        request = network.call_args.args[0]
        self.assertIn('amenity', request.data.decode())
        self.assertEqual(request.get_method(), 'POST')

    def test_hotel_no_fake_prices_or_filter(self):
        data = {'elements': [
            {'type': 'way', 'id': 77, 'center': {'lat': -37.80, 'lon': 144.96},
             'tags': {'name': 'Sample Mapped Hotel'}}
        ]}
        with patch.object(osm_places, 'urlopen', return_value=Response(json.dumps(data).encode())):
            result = run_tool('search_hotels', {'city': '墨尔本', 'max_price_aud': 150})
        self.assertEqual(result['hotels'][0]['osm_url'], 'https://www.openstreetmap.org/way/77')
        self.assertFalse(result['price_filter_applied'])
        self.assertNotIn('nightly_aud', result['hotels'][0])

    def test_server_failure_does_not_fall_back_to_fabricated_businesses(self):
        with patch.object(osm_places, 'urlopen', side_effect=TimeoutError()):
            result = run_tool('search_activities', {'city': '墨尔本', 'interest': '咖啡'})
        self.assertIn('error', result)
        self.assertNotIn('activities', result)

    def test_unknown_city_not_sent_to_public_api(self):
        with patch.object(osm_places, 'urlopen') as network:
            result = run_tool('search_activities', {'city': '纽约', 'interest': '咖啡'})
        self.assertIn('error', result)
        network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
