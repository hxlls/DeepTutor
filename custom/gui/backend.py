# -*- coding: utf-8 -*-
"""DeepTutor 试卷助手 —— 后端核心逻辑。

面向"完全不懂配置"的用户：检测环境、部署本地模型、写入配置、导入文档。
所有操作都通过 import 调用 DeepTutor 内部 API，不修改其任何文件。

被 gui/app.py 通过 pywebview 的 js_api 暴露给前端。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ---------- 路径 ----------

def repo_root() -> Path:
    """DeepTutor 仓库根目录（本文件位于 <root>/custom/gui/backend.py）。"""
    return Path(__file__).resolve().parents[2]


ROOT = repo_root()
SETTINGS = ROOT / 'data' / 'user' / 'settings'
CATALOG = SETTINGS / 'model_catalog.json'

OLLAMA_URL = 'http://127.0.0.1:11434'
EMBED_MODEL = 'bge-m3'
EMBED_DIM = '1024'

# MiMo（唯一需要用户填的东西）
MIMO_BASE = 'https://api.xiaomimimo.com/v1'
MIMO_MODELS = {
    'mimo-v2.6-flash': 'MiMo V2.6 Flash（便宜，日常够用）',
    'mimo-v2.6-pro': 'MiMo V2.6 Pro（更强，贵一些）',
}

VISION_PROMPT = """这是一份中国中学试卷中的图片。请判断内容：
- 数学公式/表达式：输出 LaTeX（行内 $...$，独立 $$...$$）
- 几何图形/函数图像：一句话说明图形类型、标注字母与已知条件
- 装饰/水印/logo/空白：输出 EMPTY
只输出结果本身，不要解释。"""


def _log(msg, on_progress=None):
    if on_progress:
        on_progress(str(msg))


# ---------- 环境检测 ----------

def find_ollama() -> Path | None:
    """定位 ollama 可执行文件。"""
    # 1. 自带的便携目录
    for cand in (ROOT / 'runtime' / 'ollama' / 'bin' / 'ollama',
                 ROOT / 'runtime' / 'ollama' / 'ollama'):
        if cand.exists():
            return cand
    # 2. 用户目录
    home = Path.home()
    for cand in (home / 'ollama' / 'bin' / 'ollama',
                 home / 'ollama' / 'ollama'):
        if cand.exists():
            return cand
    # 3. PATH
    exe = shutil.which('ollama')
    return Path(exe) if exe else None


def ollama_running() -> bool:
    try:
        with urllib.request.urlopen(f'{OLLAMA_URL}/api/tags', timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def list_local_models() -> list[str]:
    try:
        with urllib.request.urlopen(f'{OLLAMA_URL}/api/tags', timeout=5) as r:
            data = json.loads(r.read())
        return [m.get('name', '') for m in data.get('models', [])]
    except Exception:
        return []


def has_embed_model() -> bool:
    return any(m.startswith(EMBED_MODEL) for m in list_local_models())


def read_mimo_key() -> str:
    """从配置里读回已保存的 key（只返回是否已设置 + 尾部片段）。"""
    try:
        cat = json.loads(CATALOG.read_text(encoding='utf-8'))
        for c in cat.get('connections', []):
            if c.get('provider') == 'xiaomi_mimo':
                k = c.get('api_key', '')
                if k:
                    return k
    except Exception:
        pass
    return ''


def check_env() -> dict:
    """一次性检测全部环境状态，供界面显示。"""
    ollama = find_ollama()
    key = read_mimo_key()
    return {
        'python': sys.version.split()[0],
        'platform': platform.system(),
        'repo_root': str(ROOT),
        'ollama_found': bool(ollama),
        'ollama_path': str(ollama) if ollama else '',
        'ollama_running': ollama_running(),
        'models': list_local_models(),
        'embed_ready': has_embed_model(),
        'mimo_key_set': bool(key),
        'mimo_key_hint': (f'...{key[-6:]}' if key else ''),
        'kb_ready': (ROOT / 'data' / 'knowledge_bases').exists(),
    }


# ---------- 部署本地模型 ----------

def start_ollama(on_progress=None) -> bool:
    """后台启动 ollama serve。"""
    if ollama_running():
        _log('Ollama 已在运行', on_progress)
        return True
    exe = find_ollama()
    if not exe:
        _log('未找到 Ollama，请先安装', on_progress)
        return False

    _log('正在启动 Ollama 服务...', on_progress)
    kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if platform.system() == 'Windows':
        kwargs['creationflags'] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
    else:
        kwargs['start_new_session'] = True
    subprocess.Popen([str(exe), 'serve'], **kwargs)

    for _ in range(20):
        time.sleep(1)
        if ollama_running():
            _log('Ollama 服务已就绪', on_progress)
            return True
    _log('Ollama 启动超时', on_progress)
    return False


def pull_embed_model(on_progress=None) -> bool:
    """拉取 embedding 模型（流式进度）。"""
    exe = find_ollama()
    if not exe:
        _log('未找到 Ollama', on_progress)
        return False
    if has_embed_model():
        _log(f'{EMBED_MODEL} 已就绪，跳过下载', on_progress)
        return True

    _log(f'开始下载 {EMBED_MODEL}（约 1.2 GB，视网速 1-10 分钟）...', on_progress)
    proc = subprocess.Popen(
        [str(exe), 'pull', EMBED_MODEL],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace',
    )
    last = ''
    for line in proc.stdout:
        line = line.strip()
        if line and line != last:
            last = line
            _log(line[:120], on_progress)
    proc.wait()
    ok = proc.returncode == 0 and has_embed_model()
    _log('模型下载完成' if ok else '模型下载失败', on_progress)
    return ok


# ---------- 写配置 ----------

def save_mimo_key(api_key: str, model: str = 'mimo-v2.6-flash') -> dict:
    """写入 MiMo key + 本地 embedding 配置。"""
    api_key = (api_key or '').strip()
    if not api_key:
        return {'ok': False, 'msg': 'API Key 不能为空'}

    SETTINGS.mkdir(parents=True, exist_ok=True)
    if CATALOG.exists():
        cat = json.loads(CATALOG.read_text(encoding='utf-8'))
    else:
        # 触发官方初始化生成骨架
        try:
            from deeptutor.services.setup.init import init_user_directories
            init_user_directories()
        except Exception:
            pass
        cat = json.loads(CATALOG.read_text(encoding='utf-8')) \
            if CATALOG.exists() else {'version': 1, 'connections': [], 'services': {}}

    # --- 1. 连接 ---
    conns = cat.setdefault('connections', [])
    conns[:] = [c for c in conns if c.get('id') not in ('xiaomi-mimo', 'ollama-local')]
    conns.append({
        'id': 'xiaomi-mimo', 'name': 'Xiaomi MiMo', 'provider': 'xiaomi_mimo',
        'api_key': api_key, 'base_url': MIMO_BASE, 'api_version': '',
    })
    conns.append({
        'id': 'ollama-local', 'name': 'Ollama 本地', 'provider': 'ollama',
        'api_key': 'ollama', 'base_url': f'{OLLAMA_URL}/v1', 'api_version': '',
    })

    services = cat.setdefault('services', {})

    def shell():
        return {'active_profile_id': None, 'active_model_id': None, 'profiles': []}

    # --- 2. LLM + task（MiMo）---
    for svc_name in ('llm', 'task'):
        svc = services.setdefault(svc_name, shell())
        if not isinstance(svc.get('profiles'), list):
            svc['profiles'] = []
        pid = f'mimo-{svc_name}'
        svc['profiles'] = [p for p in svc['profiles'] if p.get('id') != pid]
        svc['profiles'].append({
            'id': pid, 'name': f'MiMo（{svc_name}）', 'provider': 'xiaomi_mimo',
            'base_url': MIMO_BASE, 'api_key': api_key,
            'models': [{
                'id': model, 'name': model, 'model': model,
                'capabilities': {'tools': True, 'vision': True,
                                 'json_output': True, 'reasoning': True},
            }],
        })
        svc['active_profile_id'] = pid
        svc['active_model_id'] = model

    # --- 3. embedding（本地 Ollama）---
    emb = services.setdefault('embedding', shell())
    if not isinstance(emb.get('profiles'), list):
        emb['profiles'] = []
    emb['profiles'] = [p for p in emb['profiles'] if p.get('id') != 'ollama-bge-m3']
    emb['profiles'].append({
        'id': 'ollama-bge-m3', 'name': 'BGE-M3（本地）', 'provider': 'ollama',
        'base_url': f'{OLLAMA_URL}/v1/embeddings', 'api_key': 'ollama',
        'models': [{'id': EMBED_MODEL, 'name': 'BGE-M3',
                    'model': EMBED_MODEL, 'dimension': EMBED_DIM,
                    'send_dimensions': False}],
    })
    emb['active_profile_id'] = 'ollama-bge-m3'
    emb['active_model_id'] = EMBED_MODEL

    CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=2),
                       encoding='utf-8')
    return {'ok': True, 'msg': '配置已保存'}


def test_mimo(api_key: str, model: str = 'mimo-v2.6-flash') -> dict:
    """测试 MiMo key 是否可用。"""
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': '回复两个字：正常'}],
        'max_tokens': 20,
    }
    req = urllib.request.Request(
        f'{MIMO_BASE}/chat/completions',
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {api_key}',
                 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        txt = data['choices'][0]['message'].get('content', '')
        return {'ok': True, 'msg': f'连接正常：{txt.strip()[:30]}'}
    except Exception as e:
        return {'ok': False, 'msg': f'连接失败：{str(e)[:120]}'}
