# -*- coding: utf-8 -*-
"""把本地 Ollama embedding 写入 DeepTutor 配置。

用法:
  python3 custom/config/setup_local_embedding.py            # 写入
  python3 custom/config/setup_local_embedding.py --check    # 只看当前配置

原理:
  DeepTutor 的 embedding 适配器（adapters/ollama.py）直接把 profile 的
  base_url 当作 POST 地址，所以这里要填完整的 /api/embed 端点。
  （connection 层会给 embedding 追加 "/embeddings" 后缀，因此不用它。）
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / 'data' / 'user' / 'settings' / 'model_catalog.json'

CONNECTION_ID = 'ollama-local'
PROFILE_ID = 'ollama-bge-m3'
MODEL_ID = 'bge-m3'
# Ollama 在 DeepTutor 里注册为 openai_compat 后端（见 provider_registry.py），
# 默认 base 是 http://localhost:11434/v1，所以要走 OpenAI 兼容端点。
# connection 层会给 embedding 追加 "/embeddings" 后缀 → /v1/embeddings
OLLAMA_BASE = 'http://127.0.0.1:11434/v1'
EMBED_URL = f'{OLLAMA_BASE}/embeddings'
MODEL_NAME = 'bge-m3'
DIMENSION = '1024'
# Ollama 不校验 API key，但 DeepTutor 要求非空（provider_mode 默认 standard）
PLACEHOLDER_KEY = 'ollama'


def main():
    sys.stdout.reconfigure(encoding='utf-8')

    if not CATALOG.exists():
        print(f'配置文件不存在: {CATALOG}')
        print('请先在仓库根目录执行: .venv/bin/deeptutor init')
        sys.exit(1)

    cat = json.loads(CATALOG.read_text(encoding='utf-8'))

    if '--check' in sys.argv:
        emb = cat.get('services', {}).get('embedding', {})
        print('=== 当前 embedding 配置 ===')
        print(f"active_profile_id: {emb.get('active_profile_id')}")
        print(f"profiles: {json.dumps(emb.get('profiles', []), ensure_ascii=False, indent=2)}")
        print()
        print('=== connections ===')
        print(json.dumps(cat.get('connections', []), ensure_ascii=False, indent=2))
        return

    # 1. connection（凭证来源，供其他服务复用）
    #    Ollama 不校验 key，但 DeepTutor 的 provider_mode 解析把它当 standard，
    #    因此必须给一个非空占位值，否则报 "Embedding API key not set"。
    conns = cat.setdefault('connections', [])
    conns[:] = [c for c in conns if c.get('id') != CONNECTION_ID]
    conns.append({
        'id': CONNECTION_ID,
        'name': 'Ollama 本地',
        'provider': 'ollama',
        'api_key': PLACEHOLDER_KEY,
        'base_url': OLLAMA_BASE,
        'api_version': '',
    })

    # 2. embedding profile
    #    注意结构是三层：services.embedding.profiles[].models[]
    #    —— profile 下还有一层 models 数组，model_id 指向的是 models[].id
    services = cat.setdefault('services', {})
    emb = services.setdefault('embedding', {
        'active_profile_id': None, 'active_model_id': None, 'profiles': [],
    })
    profiles = emb.setdefault('profiles', [])
    if isinstance(profiles, dict):          # 兼容旧格式
        profiles = list(profiles.values())
    profiles = [p for p in profiles if p.get('id') != PROFILE_ID]
    profiles.append({
        'id': PROFILE_ID,
        'name': 'BGE-M3（本地 Ollama）',
        'provider': 'ollama',
        'base_url': EMBED_URL,              # 完整端点，适配器直接 POST
        'api_key': PLACEHOLDER_KEY,
        'models': [
            {
                'id': MODEL_ID,
                'name': 'BGE-M3',
                'model': MODEL_NAME,
                'dimension': DIMENSION,
                'send_dimensions': False,
            },
        ],
    })
    emb['profiles'] = profiles
    emb['active_profile_id'] = PROFILE_ID
    emb['active_model_id'] = MODEL_ID

    CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=2),
                       encoding='utf-8')
    print('✓ 配置已写入')
    print(f'  connection: {CONNECTION_ID} -> {OLLAMA_BASE}')
    print(f'  embedding : {PROFILE_ID} -> {EMBED_URL} ({MODEL_NAME}, {DIMENSION} 维)')
    print()
    print('验证: python3 custom/config/setup_local_embedding.py --check')


if __name__ == '__main__':
    main()
