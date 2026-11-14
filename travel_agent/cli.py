"""Travel Agent Stage 2: persisted session, explicit preferences, safe trace, local skill.

This is a local educational prototype, NOT an authenticated service.
Hotel/activity data is fictional, with no booking or live availability.
"""
import argparse
import json
import hashlib
import os
from time import perf_counter

from common import load_history, save_history, model_text, json_from_model, session_file
from travel_agent.memory import (
    forget_profile, load_profile, remember_explicit_preferences,
)
from travel_agent.skills import load_answer_skill
from travel_agent.tools import run_tool
from travel_agent.trace import new_run_id, record_event
from model_providers import AllProvidersFailed

MAX_STEPS = 6


def _history_key(user_id, session_id):
    # Length-limited stable key, with no user-supplied path separators.
    session_file('travel_profiles', user_id)
    session_file('travel', session_id)
    digest = hashlib.sha256(f'{user_id}\0{session_id}'.encode('utf-8')).hexdigest()[:32]
    return f's_{digest}'

def tool_help():
    if os.getenv('TRAVEL_DATA_MODE', 'mock').strip().lower() == 'osm':
        return '''
search_hotels: {"city":"墨尔本"} -> OpenStreetMap 市中心真实酒店位置（无实时房价/空房）
search_activities: {"city":"墨尔本", "interest":"咖啡"} -> OpenStreetMap 咖啡馆/景点位置（无营业时间）
plan_trip: {"city":"墨尔本","days":3,"interest":"咖啡","daily_budget_aud":150} -> 真实地点三日行程与步行估计（可能缺失）
estimate_budget: {"days":3, "people":1, "daily_budget_aud":150} -> 非实时的简单预算算术
'''
    return '''
search_hotels: {"city": "墨尔本", "max_price_aud": 200} -> 本地虚构酒店样例
search_activities: {"city": "墨尔本", "interest": "咖啡"} -> 本地示例活动
estimate_budget: {"days": 3, "people": 1, "daily_budget_aud": 150} -> 示例预算估算
'''



def respond(message, session_id='demo', user_id='demo_user'):
    # Check identifiers before writing any files. The CLI ID is not authentication.
    history_key = _history_key(user_id, session_id)
    history = load_history('travel', history_key)
    profile = remember_explicit_preferences(user_id, message)
    skill = load_answer_skill()
    mode = os.getenv('TRAVEL_DATA_MODE', 'mock').strip().lower()
    log = []
    final = None
    run_id = new_run_id()
    record_event(user_id, session_id, run_id, 'run_start', status='started')

    for step in range(MAX_STEPS):
        prompt = f'''你是一个旅游规划 Agent（学习演示版本）。
任务：理解用户要求，根据需要调用一个演示工具，得到结果后继续决策。用户要求完整的多日行程时，优先使用 plan_trip 一次，而不是逐一查询商家。
本项目没有实时航班、天气、交通、价格或可预订性数据。
当前地点模式：{mode}。mock=虚构演示；osm=OpenStreetMap 有名称的真实地点记录（可能不完整/过时）。
如果收到 osm 地点数据，应优先使用真实地点名称、坐标与对应的 osm_url，说明来源并引用链接；不得编造营业时间、价格或可订情况。只有 plan_trip 的 route.status 为 ok 才能引用路线距离和预计步行分钟数；其他情况下不得猜测步行时间。
未查询到相关地点时不要伪造实际商户；其他日程安排仅能给出待核实建议。
以下本地 Skill 必须遵守：\n{skill}
用户已明确表达的旅行偏好：{json.dumps(profile, ensure_ascii=False)}
当前会话最近对话：{json.dumps(history[-8:], ensure_ascii=False)}
本轮用户问题：{message}
已经调用的工具及结果（不要重复相同参数）：{json.dumps(log, ensure_ascii=False)}
可调用工具：{tool_help()}
只输出一种 JSON 对象，不要 Markdown 或额外文字：
1. {{"action":"tool", "name":"search_activities", "arguments":{{"city":"墨尔本","interest":"咖啡"}}}}
2. {{"action":"final", "answer":"给用户的中文回答或必要澄清问题"}}
禁止使用未列出的工具；如果已经有足够证据，选择 final。'''
        started = perf_counter()
        try:
            raw = model_text(prompt)
            action = json_from_model(raw)
        except (ValueError, json.JSONDecodeError):
            record_event(user_id, session_id, run_id, 'model_response', step=step+1, status='invalid_json', duration_ms=round((perf_counter()-started)*1000))
            final = '模型未按约定输出 JSON，无法安全执行工具。请重新提问。'
            break
        except AllProvidersFailed as exc:
            record_event(user_id, session_id, run_id, 'model_response', step=step+1, status='error', duration_ms=round((perf_counter()-started)*1000))
            final = str(exc)
            break
        except Exception:
            record_event(user_id, session_id, run_id, 'model_response', step=step+1, status='error', duration_ms=round((perf_counter()-started)*1000))
            final = '模型调用出现未预期错误。请检查依赖与配置。'
            break
        record_event(user_id, session_id, run_id, 'model_response', step=step+1, status='ok', action=str(action.get('action'))[:32], duration_ms=round((perf_counter()-started)*1000))
        if action.get('action') == 'final':
            answer = action.get('answer')
            final = str(answer).strip() if isinstance(answer, str) and answer.strip() else '暂时无法回答，请补充需求。'
            break
        if action.get('action') != 'tool':
            final = '模型返回了未知动作，已停止本轮。'
            break
        name, args = action.get('name'), action.get('arguments', {})
        try:
            signature = json.dumps({'name': name, 'arguments': args}, ensure_ascii=False, sort_keys=True)
        except (ValueError, TypeError):
            final = '模型返回了不合法的工具参数，本轮停止。'
            break
        if any(item['signature'] == signature for item in log):
            final = '模型重复调用了相同工具，已停止循环。'
            break
        started = perf_counter()
        try:
            output = run_tool(name, args)
        except Exception:
            output = {'error': '演示工具执行失败'}
        record_event(user_id, session_id, run_id, 'tool_execute', step=step+1, tool=str(name)[:64], status='error' if isinstance(output, dict) and 'error' in output else 'ok', duration_ms=round((perf_counter()-started)*1000))
        print(f'  [{"地图工具" if mode == "osm" else "演示工具"} {step+1}] {name}: {json.dumps(output, ensure_ascii=False)}')
        log.append({'signature': signature, 'name': name, 'arguments': args, 'output': output})
    if final is None:
        final = '本轮工具调用达到演示上限，请缩小问题范围后重试。'
    history.extend([{'role': 'user', 'content': message}, {'role': 'assistant', 'content': final}])
    save_history('travel', history_key, history)
    record_event(user_id, session_id, run_id, 'run_end', status='completed')
    return final


def main():
    parser = argparse.ArgumentParser(description='Travel AI Agent Stage 2: 本地旅行助手')
    parser.add_argument('--user-id', default='demo_user', help='本地模拟用户ID，不是登录认证')
    parser.add_argument('--session-id', default='demo', help='本地会话ID')
    args = parser.parse_args()
    history_key = _history_key(args.user_id, args.session_id)
    print('Travel AI Agent Stage 4 | quit 退出 | clear 清空当前对话 | profile 查看偏好 | forget 清空偏好')
    if os.getenv('TRAVEL_DATA_MODE', 'mock').strip().lower() == 'osm':
        print('地图模式：查询 OpenStreetMap 贡献者提供的真实地点记录；非价格/预订/营业状态查询。')
        print('来源：© OpenStreetMap contributors (ODbL), https://www.openstreetmap.org/copyright')
    else:
        print('注意：酒店与活动为虚构演示数据；设置 TRAVEL_DATA_MODE=osm 可查询地图地点。')
    while True:
        message = input('\n你：').strip()
        if not message:
            continue
        if message.lower() == 'quit':
            break
        if message.lower() == 'clear':
            save_history('travel', history_key, [])
            print('已清空当前会话，用户偏好仍保留。')
            continue
        if message.lower() == 'profile':
            print('当前偏好：' + json.dumps(load_profile(args.user_id), ensure_ascii=False))
            continue
        if message.lower() == 'forget':
            forget_profile(args.user_id)
            print('已清空本地旅行偏好文件。')
            continue
        print('\nAgent：' + respond(message, args.session_id, args.user_id))


if __name__ == '__main__':
    main()
