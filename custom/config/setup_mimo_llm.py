# -*- coding: utf-8 -*-
"""配置 Xiaomi MiMo 作为 DeepTutor 的 LLM。

用法:
  python3 custom/config/setup_mimo_llm.py --api-key sk-xxxx
  python3 custom/config/setup_mimo_llm.py --api-key sk-xxxx --model mimo-v2.6-pro
  python3 custom/config/setup_mimo_llm.py --check

MiMo 2.6 是原生全模态模型（文本/图像/音频/视频），走标准 OpenAI 兼容协议。
默认用 flash（便宜），需要更强推理时换 pro。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / 'data' / 'user' / 'settings' / 'model_catalog.json'

CONNECTION_ID = 'xiaomi-mimo'
MIMO_BASE = 'https://api.xiaomimimo.com/v1'

# 模型定义：(model_id, 显示名, 是否标记视觉能力)
MODELS = {
    'mimo-v2.6-flash': ('MiMo V2.6 Flash', True),
    'mimo-v2.6-pro': ('MiMo V2.6 Pro', True),
}


def _service_shell():
    return {'active_profile_id': None, 'active_model_id': None, 'profiles': []}


def upsert_service(cat, service_name, model_id, api_key):
    """把 MiMo 写进指定 service 的 profiles。"""
    display, has_vision = MODELS[model_id]
    services = cat.setdefault('services', {})
    svc = services.setdefault(service_name, _service_shell())
    if not isinstance(svc.get('profiles'), list):
        svc['profiles'] = []

    profile_id = f'mimo-{service_name}'
    svc['profiles'] = [p for p in svc['profiles'] if p.get('id') != profile_id]

    caps = {'tools': True, 'json_output': True, 'reasoning': True}
    if has_vision:
        caps['vision'] = True

    svc['profiles'].append({
        'id': profile_id,
        'name': f'{display}（{service_name}）',
        'provider': 'xiaomi_mimo',
        'base_url': MIMO_BASE,
        'api_key': api_key,
        'models': [
            {
                'id': model_id,
                'name': display,
                'model': model_id,
                'capabilities': caps,
            },
        ],
    })
    svc['active_profile_id'] = profile_id
    svc['active_model_id'] = model_id


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='配置 MiMo 作为 DeepTutor LLM')
    ap.add_argument('--api-key', help='MiMo API Key')
    ap.add_argument('--model', default='mimo-v2.6-flash',
                    choices=list(MODELS), help='模型（默认 flash）')
    ap.add_argument('--check', action='store_true', help='只查看当前配置')
    args = ap.parse_args()

    if not CATALOG.exists():
        print(f'配置文件不存在: {CATALOG}')
        sys.exit(1)

    cat = json.loads(CATALOG.read_text(encoding='utf-8'))

    if args.check:
        for name in ('llm', 'task', 'embedding'):
            s = cat.get('services', {}).get(name, {})
            print(f"{name:10} active_profile={s.get('active_profile_id')} "
                  f"active_model={s.get('active_model_id')} "
                  f"profiles={len(s.get('profiles', []))}")
        print()
        for c in cat.get('connections', []):
            k = c.get('api_key', '')
            print(f"connection: {c.get('id'):20} provider={c.get('provider'):15} "
                  f"key={'<set>' if k else '<empty>'}")
        return

    if not args.api_key:
        print('缺少 --api-key。用法: python3 custom/config/setup_mimo_llm.py --api-key sk-xxx')
        sys.exit(1)

    # 1. connection（凭证中心）
    conns = cat.setdefault('connections', [])
    conns[:] = [c for c in conns if c.get('id') != CONNECTION_ID]
    conns.append({
        'id': CONNECTION_ID,
        'name': 'Xiaomi MiMo',
        'provider': 'xiaomi_mimo',
        'api_key': args.api_key,
        'base_url': MIMO_BASE,
        'api_version': '',
    })

    # 2. llm（主推理）+ task（后台轻量任务，如会话命名）
    upsert_service(cat, 'llm', args.model, args.api_key)
    upsert_service(cat, 'task', args.model, args.api_key)

    CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=2),
                       encoding='utf-8')
    print('✓ MiMo 配置已写入')
    print(f'  provider : xiaomi_mimo')
    print(f'  base_url : {MIMO_BASE}')
    print(f'  model    : {args.model}')
    print(f'  视觉能力 : 已标记')
    print()
    print('验证: python3 custom/config/setup_mimo_llm.py --check')


if __name__ == '__main__':
    main()
