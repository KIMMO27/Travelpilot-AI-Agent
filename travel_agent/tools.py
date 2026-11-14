"""Read-only travel tools. Set TRAVEL_DATA_MODE=osm for mapped POIs (no prices)."""
import os
from travel_agent.osm_places import search_places

# Only demo records. Do not present results as real businesses or prices.
DEMO_CATALOG = {
    '墨尔本': {
        'hotels': [
            {'name': 'Demo Hotel M1（虚构）', 'nightly_aud': 160, 'area': '市中心示例区域'},
            {'name': 'Demo Hotel M2（虚构）', 'nightly_aud': 110, 'area': '郊区示例区域'},
        ],
        'activities': [
            {'name': '咖啡文化体验（示例活动）', 'day_hint': '第二天上午', 'tags': ['咖啡', '美食']},
            {'name': '城市艺术馆参观（示例活动）', 'day_hint': '第一天下午', 'tags': ['艺术', '室内']},
            {'name': '城市绿地散步（示例活动）', 'day_hint': '第三天上午', 'tags': ['自然', '休闲']},
        ],
    },
    '悉尼': {
        'hotels': [{'name': 'Demo Hotel S1（虚构）', 'nightly_aud': 180, 'area': '市中心示例区域'}],
        'activities': [
            {'name': '海滨步行体验（示例活动）', 'day_hint': '第一天下午', 'tags': ['自然', '休闲']},
            {'name': '咖啡体验（示例活动）', 'day_hint': '第二天上午', 'tags': ['咖啡', '美食']},
        ],
    },
}


def _city(raw):
    text = str(raw or '').lower()
    if 'melbourne' in text or '墨尔本' in text:
        return '墨尔本'
    if 'sydney' in text or '悉尼' in text:
        return '悉尼'
    return None


def run_tool(name, arguments):
    """Allowlist-based tool dispatcher. Only accepts local read-only demo functions."""
    if not isinstance(arguments, dict):
        return {'error': 'arguments 必须是 JSON 对象'}
    if name not in {'search_hotels', 'search_activities', 'estimate_budget', 'plan_trip'}:
        return {'error': f'不允许使用工具: {name}'}
    if name == 'plan_trip':
        from travel_agent.itinerary import plan_trip
        return plan_trip(arguments)
    if name == 'estimate_budget':
        try:
            days = int(arguments.get('days', 3))
            people = int(arguments.get('people', 1))
            daily_budget = float(arguments.get('daily_budget_aud', 150))
        except (ValueError, TypeError):
            return {'error': '预算参数不是数字'}
        if not (1 <= days <= 30 and 1 <= people <= 10 and 0 <= daily_budget <= 10000):
            return {'error': '预算参数超出演示允许范围'}
        return {
            'estimated_total_aud': round(days * people * daily_budget, 2),
            'assumption': '仅演示：按人均每日预算×天数×人数估算，非实时价格',
        }
    city = _city(arguments.get('city'))
    if city is None:
        return {'error': '目前本地演示数据仅覆盖墨尔本、悉尼；需要更真实的地区信息时必须接入外部 API'}
    mode = os.getenv('TRAVEL_DATA_MODE', 'mock').strip().lower()
    if mode not in {'mock', 'osm'}:
        return {'error': 'TRAVEL_DATA_MODE 仅接受 mock 或 osm'}
    if mode == 'osm':
        result = search_places(city, name, arguments.get('interest', ''))
        if name == 'search_hotels' and 'error' not in result:
            result['price_filter_applied'] = False
            result['price_note'] = 'OSM 不提供可靠房价；不能按 max_price_aud 过滤或判断是否有空房'
        return result
    if name == 'search_hotels':
        rows = DEMO_CATALOG[city]['hotels']
        try:
            maximum = float(arguments.get('max_price_aud', 10000))
        except (TypeError, ValueError):
            return {'error': 'max_price_aud 不是数字'}
        return {'city': city, 'data_type': 'mock', 'hotels': [x for x in rows if x['nightly_aud'] <= maximum]}
    interest = str(arguments.get('interest', '')).strip()
    rows = DEMO_CATALOG[city]['activities']
    if interest:
        matching = [x for x in rows if any(tag in interest or interest in tag for tag in x['tags'])]
        if matching:
            rows = matching
    return {'city': city, 'data_type': 'mock', 'activities': rows}
