"""Stage 6 LangGraph CLI. Does not replace the working Stage 5 CLI."""
import argparse
import os
import json

from common import save_history
from travel_agent.cli import _history_key
from travel_agent.memory import load_profile, forget_profile
from travel_agent.graph_flow import run_graph_request


def main():
    parser = argparse.ArgumentParser(description='Travel Agent Stage 6 (LangGraph)')
    parser.add_argument('--user-id', default='demo_user')
    parser.add_argument('--session-id', default='graph1')
    args = parser.parse_args()
    if os.getenv('TRAVEL_DATA_MODE', '').lower() != 'osm':
        print('请使用 TRAVEL_DATA_MODE=osm 启动，避免将模拟地点当作真实地点。')
        return
    print('Stage 6 LangGraph | quit 退出 | profile 偏好 | clear 清空当前图会话 | forget 清空偏好')
    print('数据：Geoapify/OSM 地点与路径；不提供实时价格或营业验证。')
    while True:
        try:
            message = input('\n你：').strip()
        except (EOFError, KeyboardInterrupt):
            print('\n已退出')
            break
        if not message:
            continue
        if message.lower() == 'quit':
            break
        if message.lower() == 'profile':
            print(json.dumps(load_profile(args.user_id), ensure_ascii=False))
            continue
        if message.lower() == 'clear':
            save_history('travel_graph', _history_key(args.user_id, args.session_id), [])
            print('已清空当前图会话记录，不删除长期偏好。')
            continue
        if message.lower() == 'forget':
            forget_profile(args.user_id)
            print('已清空长期偏好。')
            continue
        print('\nAgent：' + run_graph_request(message, user_id=args.user_id, session_id=args.session_id))


if __name__ == '__main__':
    main()
