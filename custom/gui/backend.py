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
import urllib.error
import urllib.request
from pathlib import Path

from custom.winutil import no_window_kwargs

# ---------- 路径 ----------

def repo_root() -> Path:
    """DeepTutor 仓库根目录（本文件位于 <root>/custom/gui/backend.py）。"""
    return Path(__file__).resolve().parents[2]


ROOT = repo_root()
SETTINGS = ROOT / 'data' / 'user' / 'settings'
CATALOG = SETTINGS / 'model_catalog.json'


def in_container() -> bool:
    """是否运行在容器里。"""
    if Path('/.dockerenv').exists():
        return True
    try:
        return 'docker' in Path('/proc/1/cgroup').read_text(errors='ignore')
    except Exception:
        return False


def ollama_host() -> str:
    """Ollama 的访问地址。

    容器内必须走 host.docker.internal —— 容器里的 127.0.0.1 是容器自己，
    碰不到宿主机上跑的 Ollama。
    """
    env = os.environ.get('OLLAMA_HOST_BASE')
    if env:
        return env.rstrip('/')
    if in_container():
        return 'http://host.docker.internal:11434'
    return 'http://127.0.0.1:11434'


OLLAMA_URL = ollama_host()
EMBED_MODEL = 'bge-m3'
EMBED_DIM = '1024'

# 支持的大模型服务（都是 OpenAI 兼容协议）
LLM_PROVIDERS = {
    'xiaomi_mimo': {
        'name': 'MiMo（小米）',
        'base_url': 'https://api.xiaomimimo.com/v1',
        # 带邀请码：双方各得 ¥10 体验金 + 首单 9 折
        'key_url': 'https://platform.xiaomimimo.com?ref=XSGMF9',
        'key_label': '去注册领 ¥10 体验金',
        'key_hint': ('邀请码 XSGMF9 · 双方各得 ¥10 API 体验金 + 首单 9 折'
                     '（注册后自动填入，体验金 40 天有效）'),
        'models': {
            'mimo-v2.6-flash': 'MiMo V2.6 Flash（便宜，日常够用）',
            'mimo-v2.6-pro': 'MiMo V2.6 Pro（更强，贵一些）',
        },
        'default': 'mimo-v2.6-flash',
        'vision': True,
    },
    'deepseek': {
        'name': 'DeepSeek',
        'base_url': 'https://api.deepseek.com',
        'key_url': 'https://platform.deepseek.com/api_keys',
        'key_label': '去注册并创建 Key',
        'key_hint': '性价比高，中文强',
        'models': {
            'deepseek-flash': 'DeepSeek V4.1 Flash（快、便宜）',
            'deepseek-v4-pro': 'DeepSeek V4.1 Pro（更强）',
        },
        'default': 'deepseek-flash',
        'vision': True,
    },
    'custom': {
        'name': '其他 OpenAI 兼容服务',
        'base_url': '',
        'key_url': '',
        'key_label': '',
        'key_hint': '自行填写 base_url 与模型名',
        'models': {},
        'default': '',
        'vision': False,
    },
}

# 云端向量服务（「全云」档用）。
#
# 与 LLM_PROVIDERS 是**两套独立的槽位** —— 用户可以用 MiMo 做对话、
# 用智谱做向量，不必是同一家。这是刻意的设计：MiMo 官方只提供
# chat completions / messages，没有任何 embedding 接口，如果强制
# 「一个服务商同时提供两者」，选 MiMo 的用户就永远用不了全云档。
#
# base_url 填「根」，代码里拼 /embeddings —— 与 DeepTutor 的
# embedding 适配器约定一致（profile 的 base_url 会被直接 POST）。
# 维度**不在这里硬编码**：各家默认值不同、还能调，写错会直接导致检索
# 失效。改为在测试连接时调一次 API，用返回向量的长度反推（见 test_embedding）。
EMBEDDING_PROVIDERS = {
    'zhipu': {
        'name': '智谱 GLM',
        'base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'key_url': 'https://open.bigmodel.cn/usercenter/apikeys',
        'key_label': '去获取 API Key',
        'key_hint': 'Embedding-3，国内直连，有免费额度',
        'models': {'embedding-3': 'Embedding-3（推荐）',
                   'embedding-2': 'Embedding-2'},
        'default': 'embedding-3',
    },
    'dashscope': {
        'name': '通义百炼（阿里云）',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'key_url': 'https://bailian.console.aliyun.com/?apiKey=1',
        'key_label': '去获取 API Key',
        'key_hint': 'text-embedding-v3，国内直连，有免费额度',
        'models': {'text-embedding-v3': 'text-embedding-v3（推荐）'},
        'default': 'text-embedding-v3',
    },
    'siliconflow': {
        'name': '硅基流动',
        'base_url': 'https://api.siliconflow.cn/v1',
        'key_url': 'https://cloud.siliconflow.cn/account/ak',
        'key_label': '去注册获取 Key',
        'key_hint': '提供 BAAI/bge-m3 等开源向量模型',
        'models': {'BAAI/bge-m3': 'BGE-M3（与本地同款，1024 维）'},
        'default': 'BAAI/bge-m3',
    },
    'openai': {
        'name': 'OpenAI',
        'base_url': 'https://api.openai.com/v1',
        'key_url': 'https://platform.openai.com/api-keys',
        'key_label': '去获取 API Key',
        'key_hint': '需要能访问外网',
        'models': {'text-embedding-3-small': 'text-embedding-3-small',
                   'text-embedding-3-large': 'text-embedding-3-large'},
        'default': 'text-embedding-3-small',
    },
    'custom': {
        'name': '其他 OpenAI 兼容服务',
        'base_url': '',
        'key_url': '',
        'key_label': '',
        'key_hint': '自行填写 base_url 与模型名（需兼容 /embeddings）',
        'models': {},
        'default': '',
    },
}


def embedding_endpoint(provider: str, base_url: str = '') -> str:
    """拼出 embedding 的完整 POST 端点。

    DeepTutor 的 embedding 适配器把 profile 的 base_url 直接当请求地址，
    所以这里必须给完整路径（不能只给根，指望它自己补）。
    """
    spec = EMBEDDING_PROVIDERS.get(provider) or {}
    base = (base_url or spec.get('base_url') or '').strip().rstrip('/')
    if not base:
        return ''
    if base.endswith('/embeddings'):
        return base
    return f'{base}/embeddings'

# 兼容旧调用
MIMO_BASE = LLM_PROVIDERS['xiaomi_mimo']['base_url']
MIMO_MODELS = LLM_PROVIDERS['xiaomi_mimo']['models']

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
    """定位 ollama 可执行文件。

    容器里 /root 可能无权限，所以每一步都要防异常 ——
    否则 Path.exists() 抛 PermissionError 会让整个 status 端点 500。
    """
    def _ok(p: Path) -> bool:
        try:
            return p.is_file()
        except (PermissionError, OSError):
            return False

    # 1. 自带的便携目录
    #    Windows 上可执行文件带 .exe 后缀，两种名字都要试
    for base in (ROOT / 'runtime' / 'ollama' / 'bin',
                 ROOT / 'runtime' / 'ollama'):
        for name in ('ollama', 'ollama.exe'):
            if _ok(base / name):
                return base / name
    # 2. 用户目录
    try:
        home = Path.home()
        for base in (home / 'ollama' / 'bin', home / 'ollama'):
            for name in ('ollama', 'ollama.exe'):
                if _ok(base / name):
                    return base / name
    except Exception:
        pass
    # 3. PATH（Windows 上 shutil.which 会按 PATHEXT 自动补 .exe）
    try:
        exe = shutil.which('ollama')
        return Path(exe) if exe else None
    except Exception:
        return None


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


def kb_count() -> int:
    """已有知识库数量。

    用于「换向量服务需要重建索引」的提示：embedding 维度变了以后，
    旧知识库的向量索引就不再匹配，检索会静默失效（不报错，只是结果不对），
    所以必须在切换前把这件事说清楚。
    """
    kb_dir = ROOT / 'data' / 'knowledge_bases'
    try:
        return sum(1 for p in kb_dir.iterdir() if p.is_dir())
    except Exception:
        return 0


# ---------- 手动下载清单（给迅雷/IDM 这类工具用） ----------
#
# 映射来自 mineru/model/registry.py。standard 档需要下面两个仓库，
# basic 档只要第一个。
MODEL_REPOS = (
    {
        'id': 'MinerU-4_models_onnx',
        'title': 'MinerU 解析模型',
        'desc': '版面分析 / 公式识别 / OCR / 表格',
        'repo': 'OpenDataLab/MinerU-4_models_onnx',
    },
    {
        'id': 'MinerU2.5-Pro-2605-1.2B-GGUF',
        'title': 'MinerU VLM 模型',
        'desc': '复杂版面的视觉语言模型（公式、图表）',
        'repo': 'jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF',
    },
)

# 向量模型的 GGUF。Ollama 官方的 bge-m3 用的就是 F16 这一份。
EMBED_GGUF_REPO = 'gpustack/bge-m3-GGUF'
EMBED_GGUF_FILE = 'bge-m3-FP16.gguf'


def modelscope_direct_url(repo: str, path: str,
                          revision: str = 'master') -> str:
    """modelscope 文件直链。

    这个地址会 302 到 CDN，返回带 Content-Length 且 Accept-Ranges: bytes，
    Range 请求返回 206 —— 迅雷/IDM 这类多线程下载工具可以直接用。
    """
    from urllib.parse import quote
    return (f'https://modelscope.cn/api/v1/models/{repo}/repo'
            f'?Revision={revision}&FilePath={quote(path, safe="")}')


def _modelscope_files(repo: str) -> list[dict]:
    """列出一个 modelscope 仓库里的文件（跳过目录）。"""
    url = (f'https://modelscope.cn/api/v1/models/{repo}/repo/files'
           f'?Revision=master&Recursive=true')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    out = []
    for f in (data.get('Data') or {}).get('Files') or []:
        p = str(f.get('Path') or '').strip()
        try:
            size = int(f.get('Size') or 0)
        except (TypeError, ValueError):
            size = 0
        if not p or size <= 0:          # 目录的 Size 是 0
            continue
        out.append({'path': p, 'size': size})
    return sorted(out, key=lambda x: -x['size'])


def model_download_manifest() -> dict:
    """手动下载清单：每个模型的文件直链 + 目标目录。

    给「想用迅雷等工具下载」的用户。程序自己下虽然也能跑，但国内网络下
    modelscope / pypi 都可能很慢，有直链能省不少事。

    注意：直链下载会**丢掉目录结构**（CDN 的 filename 参数只有文件名），
    所以每个文件都带上它在仓库里的相对路径，用户下完要按路径归位。
    """
    host_base = './models/mineru/models'
    cont_base = '/app/data/.mineru/models'

    groups = []
    total = 0
    for spec in MODEL_REPOS:
        try:
            files = _modelscope_files(spec['repo'])
        except Exception as e:  # noqa: BLE001
            groups.append({
                'id': spec['id'], 'title': spec['title'],
                'desc': spec['desc'], 'repo': spec['repo'],
                'page': f'https://modelscope.cn/models/{spec["repo"]}',
                'error': f'{type(e).__name__}: {str(e)[:120]}',
                'files': [],
            })
            continue

        size = sum(f['size'] for f in files)
        total += size
        groups.append({
            'id': spec['id'],
            'title': spec['title'],
            'desc': spec['desc'],
            'repo': spec['repo'],
            'page': f'https://modelscope.cn/models/{spec["repo"]}',
            'target_host': f'{host_base}/{spec["id"]}',
            'target_container': f'{cont_base}/{spec["id"]}',
            'size_mb': round(size / 1024 / 1024, 1),
            'files': [
                {
                    'path': f['path'],
                    'size_mb': round(f['size'] / 1024 / 1024, 2),
                    'url': modelscope_direct_url(spec['repo'], f['path']),
                }
                for f in files
            ],
        })

    return {
        'ok': True,
        'total_mb': round(total / 1024 / 1024, 1),
        'groups': groups,
        'embed_note': {
            'title': '向量模型 bge-m3（可选）',
            'desc': ('它由 Ollama 管理，格式是 Ollama 自己的 blobs，'
                     '不能直接丢文件进去。程序用 ollama pull 下载通常不慢，'
                     '建议让程序自己下；确需手动下载见下面的 GGUF。'),
            'gguf_repo': EMBED_GGUF_REPO,
            'gguf_file': EMBED_GGUF_FILE,
            'page': f'https://modelscope.cn/models/{EMBED_GGUF_REPO}',
            'url': modelscope_direct_url(EMBED_GGUF_REPO, EMBED_GGUF_FILE),
        },
    }


def embedding_configured() -> bool:
    """向量检索是否已经**配置好**（配置层面，不看模型下载进度）。

    不能用 embed_ready（本地 Ollama 是否已有 bge-m3）来代替判断，两个原因：

    1. 「全云」档本来就不跑本地 embedding，按 embed_ready 算，
       选全云的用户会被一直判定为「未完成引导」。

    2. **更要命的是**：用户在第 3 步点了部署后，bge-m3 要下几分钟。
       这期间模型还没就绪，若按「模型是否在」判断，引导会被判定为
       未完成 —— 用户走完一遍点「完成」刷新，引导又跳回第一步，来回循环。

    模型下载进度由顶部横幅展示，和「引导是否完成」是两件事，不能混。
    """
    try:
        cat = json.loads(CATALOG.read_text(encoding='utf-8'))
    except Exception:
        return False

    emb = cat.get('services', {}).get('embedding', {})
    pid = emb.get('active_profile_id')
    if not pid:
        return False

    for p in emb.get('profiles', []):
        if p.get('id') != pid:
            continue
        # 有 profile 且带模型定义，就认为配置完成 —— 不再要求模型已下载
        return bool(p.get('models'))
    return False


def check_env() -> dict:
    """一次性检测全部环境状态，供界面显示。

    每项独立容错 —— 任一检测失败都不应让整个接口 500。
    """
    try:
        ollama = find_ollama()
    except Exception:
        ollama = None
    try:
        running = ollama_running()
    except Exception:
        running = False
    try:
        models = list_local_models()
    except Exception:
        models = []
    try:
        embed_ok = any(m.startswith(EMBED_MODEL) for m in models)
    except Exception:
        embed_ok = False
    try:
        key = read_mimo_key()
    except Exception:
        key = ""
    try:
        n_kb = kb_count()
    except Exception:
        n_kb = 0
    try:
        emb_ok = embedding_configured()
    except Exception:
        emb_ok = False

    return {
        'python': sys.version.split()[0],
        'platform': platform.system(),
        'repo_root': str(ROOT),
        'ollama_found': bool(ollama),
        'ollama_path': str(ollama) if ollama else '',
        'ollama_running': running,
        'models': models,
        'embed_ready': embed_ok,
        # embed_ready 只说明「本地 Ollama 有没有 bge-m3」；
        # embed_configured 才是「向量检索到底配好没有」（含云端）。
        'embed_configured': emb_ok,
        'mimo_key_set': bool(key),
        'mimo_key_hint': (f'...{key[-6:]}' if key else ''),
        'kb_ready': (ROOT / 'data' / 'knowledge_bases').exists(),
        'kb_count': n_kb,
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
    """拉取 embedding 模型（流式进度）。

    容器里没有 ollama 二进制，改用它的 HTTP API。
    """
    if has_embed_model():
        _log(f'{EMBED_MODEL} 已就绪，跳过下载', on_progress)
        return True

    exe = find_ollama()
    if exe:
        # 本机模式：直接用命令行，进度输出更友好
        _log(f'开始下载 {EMBED_MODEL}（约 1.2 GB，视网速 1-10 分钟）...',
             on_progress)
        proc = subprocess.Popen(
            [str(exe), 'pull', EMBED_MODEL],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            **no_window_kwargs(),
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

    # 容器模式：走 HTTP API
    _log(f'通过 API 下载 {EMBED_MODEL}（约 1.2 GB）...', on_progress)
    try:
        payload = json.dumps({'name': EMBED_MODEL}).encode()
        req = urllib.request.Request(f'{OLLAMA_URL}/api/pull', data=payload,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=3600) as r:
            last = ''
            for raw in r:
                try:
                    d = json.loads(raw.decode())
                except Exception:
                    continue
                status = d.get('status', '')
                total, done = d.get('total'), d.get('completed')
                if total and done:
                    pct = int(done * 100 / total)
                    msg = f'{status} {pct}%'
                else:
                    msg = status
                if msg and msg != last:
                    last = msg
                    _log(msg, on_progress)
        ok = has_embed_model()
        _log('模型下载完成' if ok else '模型下载失败', on_progress)
        return ok
    except Exception as e:
        _log(f'下载失败: {str(e)[:150]}', on_progress)
        return False


# ---------- 写配置 ----------

def _apply_embedding_config(cat: dict, cfg: dict | None) -> tuple[bool, str]:
    """把 embedding 配置写进 catalog（就地修改）。

    cfg 为 None / {'mode': 'local'} → 本地 Ollama + bge-m3
    cfg = {'mode': 'cloud', ...}    → 云端向量服务

    返回 (是否成功, 说明或错误信息)。
    """
    conns = cat.setdefault('connections', [])
    services = cat.setdefault('services', {})
    emb = services.setdefault('embedding', {
        'active_profile_id': None, 'active_model_id': None, 'profiles': [],
    })
    if not isinstance(emb.get('profiles'), list):
        emb['profiles'] = []

    cfg = cfg or {}
    if cfg.get('mode') == 'cloud':
        ep = (cfg.get('provider') or 'custom').strip()
        spec_e = EMBEDDING_PROVIDERS.get(ep) or {}
        ekey = (cfg.get('api_key') or '').strip()
        emodel = (cfg.get('model') or spec_e.get('default') or '').strip()
        eurl = embedding_endpoint(ep, cfg.get('base_url') or '')
        try:
            edim = int(cfg.get('dimension') or 0)
        except (TypeError, ValueError):
            edim = 0

        if not ekey:
            return False, '向量服务的 API Key 不能为空'
        if not emodel or not eurl:
            return False, '向量服务的模型名与端点不能为空'
        if edim <= 0:
            return False, '向量维度未知，请先点「测试连接」'

        pid = f'{ep}-embedding'
        emb['profiles'] = [p for p in emb['profiles']
                           if p.get('id') not in (pid, 'ollama-bge-m3')]
        emb['profiles'].append({
            'id': pid,
            'name': f"{spec_e.get('name') or ep}（云端向量）",
            'provider': ep, 'base_url': eurl, 'api_key': ekey,
            'models': [{'id': emodel, 'name': emodel, 'model': emodel,
                        'dimension': str(edim), 'send_dimensions': False}],
        })
        emb['active_profile_id'] = pid
        emb['active_model_id'] = emodel
        # 全云不跑本地 Ollama，连接里就别留它
        conns[:] = [c for c in conns if c.get('id') != 'ollama-local']
        return True, f'{spec_e.get("name") or ep} / {emodel}（{edim} 维）'

    # 本地 Ollama + bge-m3（标准 / 全本地档）
    emb['profiles'] = [p for p in emb['profiles']
                       if p.get('id') != 'ollama-bge-m3']
    emb['profiles'].append({
        'id': 'ollama-bge-m3', 'name': 'BGE-M3（本地）', 'provider': 'ollama',
        'base_url': f'{OLLAMA_URL}/v1/embeddings', 'api_key': 'ollama',
        'models': [{'id': EMBED_MODEL, 'name': 'BGE-M3',
                    'model': EMBED_MODEL, 'dimension': EMBED_DIM,
                    'send_dimensions': False}],
    })
    emb['active_profile_id'] = 'ollama-bge-m3'
    emb['active_model_id'] = EMBED_MODEL
    return True, 'BGE-M3（本地 Ollama）'


def save_embedding_config(embedding: dict) -> dict:
    """只更新向量配置，不动大模型配置。

    「全云」档的流程是：第 2 步先存大模型 Key（此时 embedding 默认写成本地），
    第 3 步选全云后才改成云端。单独走这个函数，就不必为了改 embedding
    把大模型 Key 再传一遍。
    """
    if not CATALOG.exists():
        return {'ok': False, 'msg': '配置文件不存在，请先保存大模型 Key'}
    try:
        cat = json.loads(CATALOG.read_text(encoding='utf-8'))
    except Exception as e:
        return {'ok': False, 'msg': f'读取配置失败：{e}'}

    ok, msg = _apply_embedding_config(cat, embedding)
    if not ok:
        return {'ok': False, 'msg': msg}

    CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=2),
                       encoding='utf-8')
    return {'ok': True, 'msg': f'向量服务已切换为 {msg}'}


def save_llm_config(api_key: str, provider: str = 'xiaomi_mimo',
                    model: str = '', base_url: str = '',
                    embedding: dict | None = None) -> dict:
    """写入大模型 key + embedding 配置。

    provider 取 LLM_PROVIDERS 的键；'custom' 时需自带 base_url 与 model。

    embedding 决定向量检索走哪条路（三档部署的差别就在这）：
      None / {'mode': 'local'}           → 本地 Ollama + bge-m3（标准、全本地）
      {'mode': 'cloud', 'provider': …, 'api_key': …, 'model': …,
       'base_url': …, 'dimension': …}    → 云端向量服务（全云档）

    「全云」档的两个 API 是**互相独立**的槽位：对话用一个 Key，
    向量用另一个 Key，可以是两家不同的服务商。
    """
    api_key = (api_key or '').strip()
    if not api_key:
        return {'ok': False, 'msg': 'API Key 不能为空'}

    spec = LLM_PROVIDERS.get(provider)
    if not spec:
        return {'ok': False, 'msg': f'未知的服务商: {provider}'}

    base = (base_url or spec['base_url']).strip().rstrip('/')
    model = (model or spec['default']).strip()
    if not base or not model:
        return {'ok': False, 'msg': 'base_url 与模型名都不能为空'}

    SETTINGS.mkdir(parents=True, exist_ok=True)
    if CATALOG.exists():
        cat = json.loads(CATALOG.read_text(encoding='utf-8'))
    else:
        try:
            from deeptutor.services.setup.init import init_user_directories
            init_user_directories()
        except Exception:
            pass
        cat = json.loads(CATALOG.read_text(encoding='utf-8')) \
            if CATALOG.exists() else {'version': 1, 'connections': [], 'services': {}}

    # --- 1. 连接 ---
    conns = cat.setdefault('connections', [])
    conns[:] = [c for c in conns if c.get('id') not in (provider, 'ollama-local')]
    conns.append({
        'id': provider, 'name': spec['name'], 'provider': provider,
        'api_key': api_key, 'base_url': base, 'api_version': '',
    })
    conns.append({
        'id': 'ollama-local', 'name': 'Ollama 本地', 'provider': 'ollama',
        'api_key': 'ollama', 'base_url': f'{OLLAMA_URL}/v1', 'api_version': '',
    })

    services = cat.setdefault('services', {})

    def shell():
        return {'active_profile_id': None, 'active_model_id': None, 'profiles': []}

    # --- 2. LLM + task ---
    caps = {'tools': True, 'json_output': True, 'reasoning': True}
    if spec.get('vision'):
        caps['vision'] = True

    for svc_name in ('llm', 'task'):
        svc = services.setdefault(svc_name, shell())
        if not isinstance(svc.get('profiles'), list):
            svc['profiles'] = []
        pid = f'{provider}-{svc_name}'
        svc['profiles'] = [p for p in svc['profiles'] if p.get('id') != pid]
        svc['profiles'].append({
            'id': pid, 'name': f"{spec['name']}（{svc_name}）", 'provider': provider,
            'base_url': base, 'api_key': api_key,
            'models': [{'id': model, 'name': model, 'model': model,
                        'capabilities': caps}],
        })
        svc['active_profile_id'] = pid
        svc['active_model_id'] = model

    # --- 3. embedding ---
    ok, msg = _apply_embedding_config(cat, embedding)
    if not ok:
        return {'ok': False, 'msg': msg}

    CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=2),
                       encoding='utf-8')
    return {'ok': True, 'msg': f'已保存：{spec["name"]} / {model}'}


def save_mimo_key(api_key: str, model: str = 'mimo-v2.6-flash') -> dict:
    """兼容旧调用。"""
    return save_llm_config(api_key, 'xiaomi_mimo', model)


def test_llm(api_key: str, provider: str = 'xiaomi_mimo',
             model: str = '', base_url: str = '') -> dict:
    """测试大模型连接。"""
    spec = LLM_PROVIDERS.get(provider, {})
    base = (base_url or spec.get('base_url', '')).strip().rstrip('/')
    model = (model or spec.get('default', '')).strip()
    if not base or not model:
        return {'ok': False, 'msg': 'base_url 或模型名缺失'}

    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': '回复两个字：正常'}],
        'max_tokens': 20,
    }
    req = urllib.request.Request(
        f'{base}/chat/completions',
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {api_key}',
                 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        txt = data['choices'][0]['message'].get('content', '')
        return {'ok': True, 'msg': f'连接正常：{txt.strip()[:30]}'}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {'ok': False, 'msg': 'API Key 无效或已过期'}
        if e.code == 404:
            return {'ok': False, 'msg': f'模型名可能不对：{model}'}
        return {'ok': False, 'msg': f'HTTP {e.code}'}
    except Exception as e:
        return {'ok': False, 'msg': f'连接失败：{str(e)[:100]}'}


def test_embedding(api_key: str, provider: str = 'zhipu', model: str = '',
                   base_url: str = '') -> dict:
    """测试云端向量服务，并**探测向量维度**。

    维度不硬编码：各家默认值不同（智谱 2048、OpenAI 1536、bge-m3 1024），
    而且大多可调，写错会直接导致知识库检索失效，且症状很隐蔽
    （不报错，只是检索结果不对）。所以这里实际调一次 API，
    用返回向量的长度反推 —— 最可靠，用户也不用去翻文档。
    """
    api_key = (api_key or '').strip()
    if not api_key:
        return {'ok': False, 'msg': 'API Key 不能为空'}

    spec = EMBEDDING_PROVIDERS.get(provider)
    if not spec:
        return {'ok': False, 'msg': f'未知的向量服务商: {provider}'}

    url = embedding_endpoint(provider, base_url)
    model = (model or spec.get('default') or '').strip()
    if not url or not model:
        return {'ok': False, 'msg': '端点或模型名不能为空'}

    req = urllib.request.Request(
        url,
        data=json.dumps({'model': model, 'input': '连通性测试'}).encode(),
        headers={'Authorization': f'Bearer {api_key}',
                 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:200]
        except Exception:
            pass
        if e.code == 401:
            return {'ok': False, 'msg': 'API Key 无效或已过期'}
        if e.code == 404:
            return {'ok': False, 'msg': f'端点或模型名可能不对：{model}'}
        return {'ok': False, 'msg': f'HTTP {e.code}：{detail or e.reason}'}
    except Exception as e:
        return {'ok': False, 'msg': f'连接失败：{str(e)[:120]}'}

    try:
        vec = resp['data'][0]['embedding']
        dim = len(vec)
    except (KeyError, IndexError, TypeError):
        return {'ok': False, 'msg': f'返回格式异常：{str(resp)[:200]}'}

    if dim <= 0:
        return {'ok': False, 'msg': '返回的向量为空'}

    return {'ok': True, 'msg': f'连接正常，向量维度 {dim}',
            'dimension': dim, 'endpoint': url}


def test_mimo(api_key: str, model: str = 'mimo-v2.6-flash') -> dict:
    """兼容旧调用。"""
    return test_llm(api_key, 'xiaomi_mimo', model)
