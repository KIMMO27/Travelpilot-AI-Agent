#!/usr/bin/env python3
"""Stage 7: reproducible, privacy-conscious evaluation for Travel Agent Stage 6.

Place in AI_Agent_Portfolio_Starter/ and run:
  python eval_travel_stage7.py --offline
  TRAVEL_MAP_PROVIDER=geoapify TRAVEL_DATA_MODE=osm python eval_travel_stage7.py --live

Offline uses a *fabricated fixture* to test metrics, NOT to claim real-world performance.
Live invokes the actual LangGraph and Geoapify tool path. A non-factual LLM greeting
is disabled so the evaluation checks source-backed planning only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse
from unittest.mock import patch

OSM_BASE = "https://www.openstreetmap.org/"
URL_RE = re.compile(r"https?://[^\s\])}>]+")


def _valid_osm(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlparse(url)
    return (parts.scheme == 'https' and parts.hostname == 'www.openstreetmap.org'
            and parts.path.startswith(('/node/', '/way/', '/relation/')))


def _metric(num: int, den: int) -> float | None:
    return round(100 * num / den, 2) if den else None


def evaluate_success(state: dict, expected_days: int) -> dict:
    """Measure candidate coverage and consistency against the *returned tool evidence*.

    Important: URL consistency does not prove that a business is open, verified, or
    that the itinerary is optimal. Route availability does not verify prices.
    """
    evidence = state.get('validated') or {}
    itinerary = evidence.get('itinerary') or []
    answer = state.get('answer', '')
    if not isinstance(answer, str):
        answer = ''
    slot_total = 3 * expected_days
    valid_slots = []
    route_days = 0
    for day in itinerary:
        if not isinstance(day, dict):
            continue
        for slot in day.get('slots', []):
            if (isinstance(slot, dict) and slot.get('status') == 'place_found'
                    and isinstance(slot.get('name'), str) and slot['name'].strip()
                    and _valid_osm(slot.get('osm_url'))):
                valid_slots.append(slot)
        route = day.get('route')
        if (isinstance(route, dict) and route.get('status') == 'ok'
                and route.get('source') == 'Geoapify Routing API'
                and all(isinstance(route.get(x), (int, float))
                        and not isinstance(route[x], bool) and 0 <= route[x] <= 1000
                        for x in ('distance_km', 'estimated_minutes'))):
            route_days += 1

    source_urls = {slot['osm_url'] for slot in valid_slots}
    answer_urls = [u.rstrip('.,;，。') for u in URL_RE.findall(answer)]
    answer_osm_urls = [u for u in answer_urls if _valid_osm(u)]
    ungrounded = sorted({u for u in answer_urls if u not in source_urls})
    linked_slots = sum(slot['osm_url'] in answer for slot in valid_slots)

    return {
        'expected_days': expected_days,
        'days_returned': len(itinerary),
        'candidate_slots_expected': slot_total,
        'candidate_slots_with_name_and_osm_link': len(valid_slots),
        'candidate_coverage_pct': _metric(len(valid_slots), slot_total),
        'days_with_geographically_routed_walk': route_days,
        'route_availability_pct': _metric(route_days, expected_days),
        'slots_with_link_in_answer': linked_slots,
        'answer_link_coverage_pct': _metric(linked_slots, len(valid_slots)),
        'ungrounded_answer_links': ungrounded,
        'answer_link_consistency_pass': bool(source_urls) and not ungrounded and linked_slots == len(valid_slots),
        'unique_location_ratio_pct': _metric(len({s['osm_url'] for s in valid_slots}), len(valid_slots)),
        'budget_verified': False,
        'interpretation': ('These metrics describe retrieved candidate completeness, routing availability '
                           'and consistency with tool output; NOT factual business verification, '
                           'booking availability, optimal routing, or budget compliance.')
    }


def offline_fixture() -> dict:
    """Synthetic data ONLY to verify that the evaluator itself behaves correctly."""
    itinerary = []
    lines = ['示例行程']
    for day in range(1, 4):
        slots = []
        for i, time_slot in enumerate(('上午', '下午', '晚上'), 1):
            oid = day * 10 + i
            name = f'仅供评估的地点 {oid}'
            url = f'https://www.openstreetmap.org/node/{oid}'
            slots.append({'slot': time_slot, 'name': name, 'osm_url': url, 'status': 'place_found'})
            lines.append(f'- {time_slot}: {name} {url}')
        itinerary.append({'day': day, 'slots': slots,
                          'route': {'status': 'ok', 'source': 'Geoapify Routing API',
                                    'distance_km': 1.5, 'estimated_minutes': 20}})
    return {'validated': {'itinerary': itinerary}, 'answer': '\n'.join(lines), 'outcome': 'success'}


def run_offline() -> dict:
    data = evaluate_success(offline_fixture(), 3)
    assert data['candidate_coverage_pct'] == 100
    assert data['route_availability_pct'] == 100
    assert data['answer_link_consistency_pass'] is True
    fixture = offline_fixture()
    fixture['answer'] += '\nhttps://www.openstreetmap.org/node/999999'
    assert evaluate_success(fixture, 3)['answer_link_consistency_pass'] is False
    fixture = offline_fixture()
    fixture['validated']['itinerary'][1]['route'] = {'status': 'unavailable'}
    assert evaluate_success(fixture, 3)['route_availability_pct'] == 66.67
    return {
        'mode': 'offline_synthetic_fixture',
        'result': 'PASS',
        'description': 'Only validates metric calculations, not any live travel system.',
        'example_metrics': data,
    }


def run_live() -> dict:
    import os
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / '.env')
    if os.environ.get('TRAVEL_DATA_MODE', '').lower() != 'osm':
        raise RuntimeError('请使用 TRAVEL_DATA_MODE=osm 以免把 mock 地点当作真实结果。')
    if os.environ.get('TRAVEL_MAP_PROVIDER', '').lower() != 'geoapify':
        raise RuntimeError('请使用 TRAVEL_MAP_PROVIDER=geoapify。')
    if not os.environ.get('GEOAPIFY_API_KEY'):
        raise RuntimeError('当前没有检测到 GEOAPIFY_API_KEY；请检查项目内 .env。')

    from travel_agent import graph_flow as graph
    import uuid

    cases = [
        ('melbourne_3d', '帮我规划墨尔本3天旅行，每天预算150澳元，我喜欢咖啡。', 'success'),
        ('unsupported_city', '帮我规划伦敦3天旅行，我喜欢咖啡。', 'failed'),
        ('unsupported_duration', '帮我规划墨尔本9天旅行，我喜欢咖啡。', 'failed'),
    ]
    results = []
    for ident, prompt, expected in cases:
        state = {'message': prompt,
                 'user_id': 'stage7_eval_only',
                 'session_id': 'evaluation',
                 'run_id': 'eval_' + uuid.uuid4().hex[:12]}
        start = perf_counter()
        # The Graph's optional short LLM greeting is not a part of route quality.
        # Disable for cost, repeatability, and to focus on verified tool data.
        with patch.object(graph, 'model_text_with_fallback', return_value=''):
            output = graph.build_graph().invoke(state)
        duration = round(perf_counter() - start, 2)
        outcome = output.get('outcome', 'unknown')
        case = {'case': ident, 'expected_outcome': expected,
                'actual_outcome': outcome, 'outcome_matches_expectation': outcome == expected,
                'wall_time_seconds': duration}
        if expected == 'success':
            measured = evaluate_success(output, expected_days=3)
            case['metrics'] = measured
            case['pass'] = outcome == 'success' and measured['candidate_coverage_pct'] == 100 and measured['answer_link_consistency_pass']
        else:
            case['pass'] = (outcome == 'failed' and not output.get('validated') and not any(
                _valid_osm(u) for u in URL_RE.findall(output.get('answer', ''))))
        results.append(case)
        print(f"{ident}: {'PASS' if case['pass'] else 'FAIL'} | outcome={outcome} | {duration}s")
    return {'mode': 'live_geoapify_langgraph', 'cases': results,
            'cases_passed': sum(c['pass'] for c in results), 'cases_total': len(results),
            'success_rate_pct': _metric(sum(c['pass'] for c in results), len(results)),
            'notes': ['One real routing case and two negative scenarios; not a statistical quality benchmark.',
                      'The model greeting is disabled during evaluation.',
                      'Geoapify responses may come from local cache.',
                      'No live opening-hour, booking, fare, or budget validation was performed.']}


def main() -> int:
    parser = argparse.ArgumentParser(description='Stage 7 Travel Agent evaluation')
    exclusive = parser.add_mutually_exclusive_group(required=True)
    exclusive.add_argument('--offline', action='store_true', help='Only test evaluator using synthetic fixture')
    exclusive.add_argument('--live', action='store_true', help='Run LangGraph against Geoapify/cache')
    args = parser.parse_args()
    try:
        payload = run_offline() if args.offline else run_live()
    except Exception as e:
        print(f'评估中止：{type(e).__name__}: {str(e)[:220]}', file=sys.stderr)
        return 1
    payload['timestamp_utc'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    outdir = Path('evaluation') / 'reports'
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / ('stage7_offline.json' if args.offline else 'stage7_live.json')
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'评估报告已保存：{path}')
    if args.live:
        print(f"测试通过：{payload['cases_passed']}/{payload['cases_total']}")
        for case in payload['cases']:
            if 'metrics' in case:
                m = case['metrics']
                print(f"有效地点覆盖率：{m['candidate_coverage_pct']}%")
                print(f"步行路线可用率：{m['route_availability_pct']}%")
                print(f"回答链接与工具结果一致：{m['answer_link_consistency_pass']}")
        return 0 if payload['cases_passed'] == payload['cases_total'] else 2
    else:
        print('离线样本评估逻辑检查：PASS（使用的是合成样例，不是真实效果）')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
