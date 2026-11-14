"""Bounded, cached OpenStreetMap POI queries for a local travel-agent demo.

Not a source of live opening hours, prices, booking inventory, or directions.
A public Overpass endpoint can fail even for correct requests; use multiple
best-effort endpoints, strict timeouts, and truthful error handling.

Data © OpenStreetMap contributors (ODbL):
https://www.openstreetmap.org/copyright
"""
import hashlib
import json
import os
import ssl
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# City-centre coordinates are public reference points, not user locations.
CITY_CENTERS = {
    '墨尔本': (-37.8136, 144.9631),
    '悉尼': (-33.8688, 151.2093),
}
# Each is a third-party public, best-effort service; no guaranteed uptime.
OVERPASS_ENDPOINTS = (
    'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
    'https://overpass.private.coffee/api/interpreter',
    'https://overpass-api.de/api/interpreter',
)
CACHE_TTL = 24 * 60 * 60
STALE_CACHE_MAX_AGE = 14 * 24 * 60 * 60
MAX_DOWNLOAD = 2_000_000
NETWORK_TIMEOUT = 9  # seconds for each endpoint; fail fast rather than hang
CACHE_DIR = Path('local_osm_cache')


class OSMFetchError(Exception):
    """All public API attempts failed (the underlying details are diagnostics)."""


def _category(tool_name, interest):
    if tool_name == 'search_hotels':
        return 'hotel', 'nwr["tourism"~"^(hotel|hostel|guest_house)$"]'
    term = str(interest or '').lower()[:80]
    if any(x in term for x in ('咖啡', 'coffee', 'cafe')):
        return 'cafe', 'nwr["amenity"="cafe"]'
    if any(x in term for x in ('餐厅', '美食', 'restaurant', 'food', '吃饭')):
        return 'restaurant', 'nwr["amenity"="restaurant"]'
    if any(x in term for x in ('公园', '自然', '花园', 'park', 'garden')):
        return 'park', 'nwr["leisure"="park"]'
    return 'attraction', 'nwr["tourism"~"^(attraction|museum|gallery|viewpoint)$"]'


def _make_query(city, selector):
    lat, lon = CITY_CENTERS[city]
    # A small bounding box is cheaper for public Overpass servers than
    # fetching all nwr elements in a 3-km circle. ~1.1-km N/S + E/W.
    south, north = lat - 0.010, lat + 0.010
    west, east = lon - 0.012, lon + 0.012
    return (f'[out:json][timeout:12];'
            f'{selector}["name"]({south:.5f},{west:.5f},{north:.5f},{east:.5f});'
            f'out center 25;')


def _load_cache(path):
    try:
        if path.exists():
            obj = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(obj, dict) and isinstance(obj.get('elements'), list):
                return obj
    except (ValueError, OSError):
        pass
    return None


def _ssl_context():
    # Resolve the macOS/python virtualenv CA issue without disabling TLS
    # verification. certifi is optional; system trust is used if unavailable.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _fetch_pois(city, category, selector):
    query = _make_query(city, selector)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(query.encode('utf-8')).hexdigest()[:24]
    cached = CACHE_DIR / (key + '.json')
    old = _load_cache(cached)
    age = time.time() - cached.stat().st_mtime if old is not None else float('inf')
    if old is not None and age < CACHE_TTL:
        return old, 'cache'

    encoded = urlencode({'data': query}).encode('utf-8')
    user_agent = os.getenv('TRAVEL_OSM_USER_AGENT',
                           'AI-Agent-Portfolio/0.5 (local educational project; on-demand queries)')
    context = _ssl_context()
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        req = Request(endpoint, data=encoded, headers={
            'User-Agent': user_agent,
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
        }, method='POST')
        try:
            with urlopen(req, timeout=NETWORK_TIMEOUT, context=context) as response:
                raw = response.read(MAX_DOWNLOAD + 1)
            if len(raw) > MAX_DOWNLOAD:
                raise ValueError('响应超出大小限制')
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get('elements'), list):
                raise ValueError('响应不是 Overpass elements 数组')
            # Only validated network data is allowed into the on-disk cache.
            tmp = cached.with_suffix('.tmp')
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            tmp.replace(cached)
            return payload, 'network'
        except (HTTPError, URLError, TimeoutError, OSError,
                ValueError, json.JSONDecodeError) as exc:
            host = endpoint.split('/')[2]
            code = getattr(exc, 'code', None)
            errors.append(f'{host}: HTTP {code}' if code else f'{host}: {type(exc).__name__}')
            # No unbounded retries or sleep; one attempt per host per user query.
            continue

    if old is not None and age < STALE_CACHE_MAX_AGE:
        return old, 'stale_cache'
    raise OSMFetchError('; '.join(errors))



# Optional hosted Places API path. Do NOT run it in legacy Overpass tests:
# select explicitly with TRAVEL_MAP_PROVIDER=geoapify.
GEOAPIFY_ENDPOINT = 'https://api.geoapify.com/v2/places'
GEOAPIFY_CATEGORY = {
    'hotel': 'accommodation.hotel,accommodation.hostel,accommodation.guest_house',
    'cafe': 'catering.cafe',
    'restaurant': 'catering.restaurant',
    'park': 'leisure.park',
    'attraction': 'entertainment.museum,entertainment.culture.gallery,leisure.park',
}


def _geoapify_key():
    """Load the key locally, never include it in a URL or diagnostic output."""
    key = os.getenv('GEOAPIFY_API_KEY', '').strip()
    if not key:
        try:
            from dotenv import dotenv_values
            key = (dotenv_values('.env').get('GEOAPIFY_API_KEY') or '').strip()
        except ImportError:
            pass
    return key


def _geoapify_places(city, category, tool_name):
    """Get bounded, cacheable real POIs from Geoapify; no mock fallback."""
    key = _geoapify_key()
    if not key:
        return {'error': 'Geoapify 未配置。请在项目 .env 中添加 GEOAPIFY_API_KEY。',
                'data_type': 'geoapify_error'}

    lat, lon = CITY_CENTERS[city]
    mapping = GEOAPIFY_CATEGORY.get(category, 'entertainment.museum')
    # Conservative 1.5-km radius, 10 records max; no overfetching.
    body = {
        'categories': mapping.split(','),
        'filter': {'type': 'circle', 'lon': lon, 'lat': lat, 'radius': 1500},
        'bias': {'type': 'proximity', 'lon': lon, 'lat': lat},
        'limit': 10,
        'lang': 'en',
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]
    cache_path = CACHE_DIR / f'geoapify_{fingerprint}.json'
    cached = _load_geoapify_cache(cache_path)
    age = time.time() - cache_path.stat().st_mtime if cached is not None else float('inf')
    if cached is not None and age < CACHE_TTL:
        payload, mode = cached, 'cache'
    else:
        req = Request(
            GEOAPIFY_ENDPOINT,
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={
                'User-Agent': os.getenv('TRAVEL_GEOAPIFY_USER_AGENT', 'AI-Agent-Portfolio/1.0 (local educational demo)'),
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-API-Key': key,  # header rather than URL; do not expose in logs
            },
            method='POST',
        )
        try:
            with urlopen(req, timeout=12, context=_ssl_context()) as response:
                raw = response.read(MAX_DOWNLOAD + 1)
            if len(raw) > MAX_DOWNLOAD:
                raise ValueError('Geoapify 响应体超出大小限制')
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get('features'), list):
                raise ValueError('Geoapify 未返回 GeoJSON features 数组')
            tmp = cache_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            tmp.replace(cache_path)
            mode = 'network'
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            # Do not leak API key, user prompt, or raw API responses into logs.
            if cached is not None and age < STALE_CACHE_MAX_AGE:
                payload, mode = cached, 'stale_cache'
            else:
                code = getattr(exc, 'code', None)
                detail = f'HTTP {code}' if code else type(exc).__name__
                return {'error': 'Geoapify 地点查询失败，请稍后重试；未使用虚构地点。',
                        'detail': detail, 'data_type': 'geoapify_error'}

    places, seen = [], set()
    for feature in payload['features']:
        if not isinstance(feature, dict):
            continue
        prop = feature.get('properties') or {}
        if not isinstance(prop, dict):
            continue
        name = prop.get('name')
        if not isinstance(name, str) or not name.strip():
            continue
        geometry = feature.get('geometry') or {}
        coords = geometry.get('coordinates') if isinstance(geometry, dict) else None
        if isinstance(coords, list) and len(coords) >= 2:
            point_lon, point_lat = coords[:2]
        else:
            point_lon, point_lat = prop.get('lon'), prop.get('lat')
        if not all(isinstance(v, (float, int)) and not isinstance(v, bool) for v in (point_lon, point_lat)):
            continue
        if not (-90 <= point_lat <= 90 and -180 <= point_lon <= 180):
            continue
        if name.strip().casefold() in seen:
            continue
        seen.add(name.strip().casefold())

        # Geoapify points may expose the underlying OSM element ID/type.
        source = prop.get('datasource') or {}
        raw = source.get('raw') if isinstance(source, dict) else {}
        raw = raw if isinstance(raw, dict) else {}
        osm_id = raw.get('osm_id')
        osm_type = str(raw.get('osm_type') or '').lower()
        osm_kind = {'n': 'node', 'w': 'way', 'r': 'relation',
                    'node': 'node', 'way': 'way', 'relation': 'relation'}.get(osm_type)
        try:
            osm_id = int(osm_id)
        except (ValueError, TypeError):
            osm_id = None
        if osm_kind and osm_id is not None and osm_id > 0:
            map_url = f'https://www.openstreetmap.org/{osm_kind}/{osm_id}'
        else:
            # A map VIEW link at the real returned coordinates, not a fabricated POI object ID.
            map_url = f'https://www.openstreetmap.org/#map=18/{point_lat:.6f}/{point_lon:.6f}'
        place = {
            'name': name.strip()[:120],
            'latitude': round(point_lat, 6),
            'longitude': round(point_lon, 6),
            'osm_url': map_url,
            'category': category,
        }
        if isinstance(prop.get('formatted'), str):
            place['address'] = prop['formatted'][:200]
        places.append(place)
        if len(places) >= 8:
            break

    field = 'hotels' if tool_name == 'search_hotels' else 'activities'
    return {
        'city': city,
        'data_type': 'osm',  # compatible with the existing Agent tool schema
        'source': 'Geoapify Places API (OSM-based and other published POI sources)',
        'data_source': 'Geoapify Places API',
        'fetch_mode': mode,
        'coverage': '市中心约 1.5 公里范围内返回的部分记录，非完整商户名录',
        'not_verified': '营业状态、营业时间、实时价格、空房、交通时间和预约均未核验',
        'cache_warning': '返回的是之前缓存的地点，可能过时，请核对' if mode == 'stale_cache' else '',
        field: places,
    }


def _load_geoapify_cache(path):
    """Accept only cached GeoJSON features, not arbitrary or corrupted files."""
    try:
        if path.exists():
            obj = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(obj, dict) and isinstance(obj.get('features'), list):
                return obj
    except (OSError, ValueError):
        pass
    return None

def search_places(city, tool_name, interest=''):
    """Return mapped POIs, never fabricated businesses or live availability."""
    if city not in CITY_CENTERS:
        return {'error': '地图模式目前只支持墨尔本和悉尼市中心附近',
                'data_type': 'osm_error'}
    category, selector = _category(tool_name, interest)
    if os.getenv('TRAVEL_MAP_PROVIDER', '').lower().strip() == 'geoapify':
        return _geoapify_places(city, category, tool_name)
    lat, lon = CITY_CENTERS[city]
    try:
        payload, source_mode = _fetch_pois(city, category, selector)
    except OSMFetchError as exc:
        return {
            'error': '公开地图服务本次均未成功，无法验证具体商户；未使用虚构数据',
            'detail': str(exc),
            'data_type': 'osm_error',
            'manual_map_url': f'https://www.openstreetmap.org/#map=15/{lat}/{lon}',
        }

    results, seen = [], set()
    for element in payload['elements']:
        if not isinstance(element, dict):
            continue
        tags = element.get('tags') or {}
        if not isinstance(tags, dict):
            continue
        name = tags.get('name:zh') or tags.get('name')
        kind, place_id = element.get('type'), element.get('id')
        coord = element.get('center') if kind != 'node' else element
        if not isinstance(name, str) or not name.strip() or kind not in ('node', 'way', 'relation'):
            continue
        if not isinstance(place_id, int) or not isinstance(coord, dict):
            continue
        elem_lat, elem_lon = coord.get('lat'), coord.get('lon')
        if not isinstance(elem_lat, (int, float)) or not isinstance(elem_lon, (int, float)):
            continue
        name = name.strip()[:120]
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        results.append({
            'name': name,
            'latitude': round(elem_lat, 6),
            'longitude': round(elem_lon, 6),
            'osm_url': f'https://www.openstreetmap.org/{kind}/{place_id}',
            'category': category,
        })
        if len(results) >= 8:
            break

    field = 'hotels' if tool_name == 'search_hotels' else 'activities'
    return {
        'city': city,
        'data_type': 'osm',
        'source': '© OpenStreetMap contributors (ODbL)',
        'data_source': 'public Overpass API',
        'fetch_mode': source_mode,
        'coverage': '约市中心 1 公里范围的部分有名称记录，不保证完整',
        'not_verified': '营业时间、价格、空房、交通时间、预约和实时可用性均未核验',
        'cache_warning': '数据来自之前缓存；可能过时，请核对' if source_mode == 'stale_cache' else '',
        field: results,
    }
