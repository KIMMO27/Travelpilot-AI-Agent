"""Stage 6: deterministic travel-planning workflow orchestrated by LangGraph.

Graph: parse -> plan_trip -> verify -> answer; failures -> safe_failure.
No model is allowed to invent POI names, prices or route metrics. The itinerary
is rendered from verified tool data, even when the optional LLM intro fails.
"""
from __future__ import annotations

import os
import re
from time import perf_counter
from typing import Any, TypedDict

from langgraph.graph import StateGraph, START, END

from common import BASE, load_history, save_history
from dotenv import load_dotenv
from model_providers import model_text_with_fallback
from travel_agent.cli import _history_key
from travel_agent.memory import remember_explicit_preferences, load_profile
from travel_agent.tools import run_tool
from travel_agent.trace import record_event, new_run_id


class TravelState(TypedDict, total=False):
    message: str
    user_id: str
    session_id: str
    run_id: str
    city: str
    days: int
    interest: str
    daily_budget_aud: float | None
    plan: dict[str, Any]
    validated: dict[str, Any]
    answer: str
    error: str
    outcome: str
    provider_status: str


def _event(state: TravelState, name: str, status: str, **kw: Any) -> None:
    record_event(state['user_id'], state['session_id'], state['run_id'], name, status=status, **kw)


def _parse_days(message: str) -> int | None:
    match = re.search(r'(\d{1,2})\s*(?:天|日)(?:\s*(?:旅行|旅游|行程))?', message)
    if match:
        return int(match.group(1))
    for token, value in [('五天', 5), ('四天', 4), ('三天', 3), ('两天', 2), ('二天', 2), ('一天', 1)]:
        if token in message:
            return value
    return None


def parse_requirements(state: TravelState) -> dict[str, Any]:
    t0 = perf_counter()
    text = state['message'].strip()
    # This saves only explicit user preferences; user_id is an unverified local demo ID.
    profile = remember_explicit_preferences(state['user_id'], text)
    city = ('墨尔本' if '墨尔本' in text or 'melbourne' in text.lower()
            else '悉尼' if '悉尼' in text or 'sydney' in text.lower() else '')
    days = _parse_days(text)
    interest = ('咖啡' if '咖啡' in text or 'coffee' in text.lower() else
                '咖啡' if '咖啡' in profile.get('likes', []) else '')
    budget_match = (re.search(r'(?:每天|每日|日均)\s*(?:预算|花费)?\s*(?:AUD|澳元|\$)?\s*(\d+(?:\.\d+)?)', text, flags=re.I)
                    or re.search(r'(?:预算|花费)\s*(?:每天|每日|日均)\s*(?:AUD|澳元|\$)?\s*(\d+(?:\.\d+)?)', text, flags=re.I))
    budget = float(budget_match.group(1)) if budget_match else None
    error = ''
    if not text:
        error = '请先告诉我你的旅行需求。'
    elif not city:
        error = '请说明目的地（目前支持墨尔本或悉尼市中心）。'
    elif days is None:
        error = '请说明旅行天数，例如“墨尔本3天旅行”。'
    elif not 1 <= days <= 5:
        error = '目前只支持规划1至5天的行程。'
    elif budget is not None and not 0 <= budget <= 10000:
        error = '每天预算应在0至10000澳元之间。'
    elif os.getenv('TRAVEL_DATA_MODE', 'mock').strip().lower() != 'osm':
        error = '真实行程模式需要 TRAVEL_DATA_MODE=osm；不会使用虚构地点代替。'
    _event(state, 'graph_parse', 'error' if error else 'ok', duration_ms=round((perf_counter()-t0)*1000))
    return {'city': city, 'days': days or 0, 'interest': interest,
            'daily_budget_aud': budget, 'error': error}


def route_after_parse(state: TravelState) -> str:
    return 'safe_failure' if state.get('error') else 'plan_trip'


def call_plan_trip(state: TravelState) -> dict[str, Any]:
    t0 = perf_counter()
    args = {'city': state['city'], 'days': state['days'], 'interest': state['interest']}
    if state.get('daily_budget_aud') is not None:
        args['daily_budget_aud'] = state['daily_budget_aud']
    try:
        result = run_tool('plan_trip', args)
        if not isinstance(result, dict) or 'error' in result:
            err = (result.get('error', '规划工具未返回有效结果') if isinstance(result, dict)
                   else '规划工具返回的数据格式不正确')
            _event(state, 'graph_plan', 'error', tool='plan_trip', duration_ms=round((perf_counter()-t0)*1000))
            return {'error': f'地点规划失败：{str(err)[:180]}'}
    except Exception:
        _event(state, 'graph_plan', 'error', tool='plan_trip', duration_ms=round((perf_counter()-t0)*1000))
        return {'error': '地点规划工具出现异常，无法获得可信行程。'}
    _event(state, 'graph_plan', 'ok', tool='plan_trip', duration_ms=round((perf_counter()-t0)*1000))
    return {'plan': result}


def route_after_plan(state: TravelState) -> str:
    return 'safe_failure' if state.get('error') else 'verify'


def verify_evidence(state: TravelState) -> dict[str, Any]:
    t0 = perf_counter()
    plan = state.get('plan', {})
    itinerary = plan.get('itinerary')
    if not isinstance(itinerary, list) or len(itinerary) != state.get('days'):
        error = '行程数据不完整，不能生成可靠路线。'
        _event(state, 'graph_verify', 'error', duration_ms=round((perf_counter()-t0)*1000))
        return {'error': error}
    safe_days: list[dict[str, Any]] = []
    valid_places = 0
    for idx, day in enumerate(itinerary, 1):
        if not isinstance(day, dict) or not isinstance(day.get('slots'), list):
            return {'error': '行程时段数据格式错误。'}
        slots = []
        for slot in day['slots'][:3]:
            if not isinstance(slot, dict):
                continue
            if slot.get('status') != 'place_found':
                slots.append({'slot': str(slot.get('slot', '待安排'))[:16], 'status': 'unavailable'})
                continue
            name, url = slot.get('name'), slot.get('osm_url')
            if not isinstance(name, str) or not name.strip() or not isinstance(url, str) or not url.startswith('https://www.openstreetmap.org/'):
                slots.append({'slot': str(slot.get('slot', '待安排'))[:16], 'status': 'unavailable'})
                continue
            valid_places += 1
            slots.append({'slot': str(slot.get('slot', '待安排'))[:16], 'status': 'place_found',
                          'name': name[:120].replace('\n', ' '), 'osm_url': url})
        route = day.get('route')
        safe_route: dict[str, Any] = {'status': 'unavailable'}
        if isinstance(route, dict) and route.get('status') == 'ok' and route.get('source') == 'Geoapify Routing API':
            d, m = route.get('distance_km'), route.get('estimated_minutes')
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1000 for v in (d, m)):
                safe_route = {'status': 'ok', 'distance_km': d, 'estimated_minutes': m,
                              'source': 'Geoapify Routing API'}
        safe_days.append({'day': idx, 'slots': slots, 'route': safe_route})
    if valid_places == 0:
        _event(state, 'graph_verify', 'error', duration_ms=round((perf_counter()-t0)*1000))
        return {'error': '目前没有可以核实名称和 OSM 链接的地点，请稍后重试。'}
    _event(state, 'graph_verify', 'ok', duration_ms=round((perf_counter()-t0)*1000))
    return {'validated': {'itinerary': safe_days, 'found': valid_places,
                          'slots': state['days']*3, 'budget_note': str(plan.get('budget_note', '价格未核实'))[:250],
                          'issues': plan.get('issues', []) if isinstance(plan.get('issues', []), list) else []}}


def route_after_verify(state: TravelState) -> str:
    return 'safe_failure' if state.get('error') else 'answer'


def _facts_answer(state: TravelState) -> str:
    result = state['validated']
    lines = [f"{state['city']}{state['days']}天行程（基于查询到的地点候选）"]
    for day in result['itinerary']:
        lines.append(f"\n第{day['day']}天")
        for slot in day['slots']:
            if slot['status'] == 'place_found':
                lines.append(f"- {slot['slot']}：{slot['name']}  {slot['osm_url']}")
            else:
                lines.append(f"- {slot['slot']}：地点待核实")
        route = day['route']
        if route['status'] == 'ok':
            lines.append(f"- 路线：步行约{route['distance_km']}公里、{route['estimated_minutes']}分钟（Geoapify Routing API 估算）")
        else:
            lines.append('- 路线：地图服务未提供可验证的步行距离和时间')
    lines += ['\n预算：'+result['budget_note'],
              '说明：营业时间、价格、空房与预订状态未实时核验；上午/下午/晚上仅为建议时段，路线数据是估算。']
    return '\n'.join(lines)


def generate_answer(state: TravelState) -> dict[str, Any]:
    # Keep factual itinerary deterministic. LLM can only add a short non-factual intro.
    t0 = perf_counter()
    intro = ''
    try:
        prompt = ('请用一句中文写旅行行程的友好开场白，最多25字。'
                  '不要出现数字、地点名称、价格、营业状态、具体活动或任何来源主张。只输出一句话。')
        raw = model_text_with_fallback(prompt, status=lambda _: None)
        if isinstance(raw, str) and len(raw) <= 40 and not re.search(r'\d', raw):
            intro = raw.strip().replace('\n', ' ')
    except Exception:
        pass  # The deterministic evidence-based itinerary remains usable offline.
    factual = _facts_answer(state)
    answer = (intro + '\n\n' if intro else '') + factual
    outcome = ('success' if state['validated']['found'] == state['validated']['slots'] else 'partial')
    _event(state, 'graph_answer', 'ok', duration_ms=round((perf_counter()-t0)*1000))
    return {'answer': answer, 'outcome': outcome}


def safe_failure(state: TravelState) -> dict[str, Any]:
    _event(state, 'graph_failure', 'error')
    return {'answer': str(state.get('error') or '本轮无法完成规划，请稍后重试。'), 'outcome': 'failed'}


def build_graph():
    graph = StateGraph(TravelState)
    graph.add_node('parse', parse_requirements)
    graph.add_node('plan_trip', call_plan_trip)
    graph.add_node('verify', verify_evidence)
    graph.add_node('answer', generate_answer)
    graph.add_node('safe_failure', safe_failure)
    graph.add_edge(START, 'parse')
    graph.add_conditional_edges('parse', route_after_parse, {'plan_trip': 'plan_trip', 'safe_failure': 'safe_failure'})
    graph.add_conditional_edges('plan_trip', route_after_plan, {'verify': 'verify', 'safe_failure': 'safe_failure'})
    graph.add_conditional_edges('verify', route_after_verify, {'answer': 'answer', 'safe_failure': 'safe_failure'})
    graph.add_edge('answer', END)
    graph.add_edge('safe_failure', END)
    return graph.compile()


def run_graph_request(message: str, *, user_id='demo_user', session_id='graph1') -> str:
    load_dotenv(BASE / '.env')
    key = _history_key(user_id, session_id)
    run_id = new_run_id()
    state: TravelState = {'message': message, 'user_id': user_id,
                          'session_id': session_id, 'run_id': run_id}
    _event(state, 'run_start', 'started')
    result: TravelState = state
    try:
        result = build_graph().invoke(state)
        answer = result.get('answer', '规划失败，请稍后再试。')
        outcome = result.get('outcome', 'failed')
    except Exception:
        answer, outcome = '工作流发生异常，已安全停止。', 'failed'
    history = load_history('travel_graph', key)
    history.extend([{'role': 'user', 'content': message}, {'role': 'assistant', 'content': answer}])
    save_history('travel_graph', key, history)
    _event(state, 'run_end', outcome)
    return answer
