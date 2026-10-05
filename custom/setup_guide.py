# -*- coding: utf-8 -*-
"""DeepTutor 试卷助手 —— 一次性引导脚本。

面向完全不懂配置的用户：自动检测环境、下载安装本地模型，
最后只需填入一个 MiMo API Key 即可开始使用。

用法:
  python custom/setup_guide.py          # 完整引导
  python custom/setup_guide.py --check  # 只检测，不安装
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OLLAMA_WIN = 'https://ollama.com/download/OllamaSetup.exe'
OLLAMA_LINUX = ('https://github.com/ollama/ollama/releases/latest/download/'
                'ollama-linux-amd64.tar.zst')
EMBED_MODEL = 'bge-m3'


def title(t):
    print()
    print('=' * 58)
    print(f'  {t}')
    print('=' * 58)


def step(t):
    print(f'\n▶ {t}')


def ok(t):
    print(f'  ✓ {t}')


def warn(t):
    print(f'  ! {t}')


def err(t):
    print(f'  ✗ {t}')


# ---------- 1. 基础环境 ----------

def check_python():
    step('检查 Python 环境')
    v = sys.version_info
    if v < (3, 11):
        err(f'Python 版本过低（{v.major}.{v.minor}），需要 3.11 或更高')
        return False
    ok(f'Python {v.major}.{v.minor}.{v.micro}')
    return True


def check_deeptutor():
    step('检查 DeepTutor')
    try:
        import deeptutor  # noqa: F401
        ok('DeepTutor 已就绪')
        return True
    except ImportError:
        err('未找到 DeepTutor')
        print('     请在 DeepTutor 仓库根目录运行本脚本，并先执行：')
        print('       pip install -e .')
        return False


# ---------- 2. Ollama ----------

def find_ollama():
    import shutil
    for cand in (ROOT / 'runtime' / 'ollama' / 'bin' / 'ollama',
                 ROOT / 'runtime' / 'ollama' / 'ollama',
                 Path.home() / 'ollama' / 'bin' / 'ollama',
                 Path.home() / 'ollama' / 'ollama'):
        if cand.exists():
            return cand
    exe = shutil.which('ollama')
    return Path(exe) if exe else None


def install_ollama():
    step('安装 Ollama（本地 embedding 引擎）')
    sysname = platform.system()

    if sysname == 'Windows':
        exe = ROOT / 'runtime' / 'OllamaSetup.exe'
        exe.parent.mkdir(parents=True, exist_ok=True)
        print(f'  下载 Ollama 安装包（约 700 MB）...')
        print(f'  地址: {OLLAMA_WIN}')
        try:
            _download(OLLAMA_WIN, exe)
            ok(f'已下载: {exe}')
            print('  正在静默安装...')
            subprocess.run([str(exe), '/VERYSILENT', '/NORESTART'], check=False)
            ok('安装完成（可能需要重新打开终端才能识别 ollama 命令）')
            return True
        except Exception as e:
            err(f'自动安装失败: {e}')
            print(f'  请手动下载安装: {OLLAMA_WIN}')
            return False

    # Linux / macOS
    print('  Linux 环境将下载便携版到 ~/ollama')
    print(f'  地址: {OLLAMA_LINUX}')
    dest = Path.home() / 'ollama'
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / 'ollama.tar.zst'
    try:
        _download(OLLAMA_LINUX, archive)
        ok(f'已下载 {archive.stat().st_size / 1024 / 1024:.0f} MB')
        print('  正在校验完整性...')
        r = subprocess.run(['zstd', '-t', str(archive)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            err('压缩包不完整，请重新运行本脚本')
            return False
        ok('校验通过')
        print('  正在解压...')
        subprocess.run(['tar', '--use-compress-program=unzstd',
                        '-xf', str(archive)], cwd=str(dest), check=True)
        archive.unlink(missing_ok=True)
        ok(f'解压完成: {dest}')
        return True
    except Exception as e:
        err(f'安装失败: {e}')
        print('  可手动执行:')
        print('    curl -fsSL https://ollama.com/install.sh | sh')
        return False


def _download(url, dest: Path):
    """带重试的下载。"""
    import shutil
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=60) as r, \
                    open(dest, 'wb') as f:
                shutil.copyfileobj(r, f, 1024 * 256)
            return
        except Exception as e:
            if attempt == 3:
                raise
            warn(f'第 {attempt} 次下载失败，重试中... ({str(e)[:60]})')
            time.sleep(3)


def start_ollama():
    step('启动 Ollama 服务')
    if _ollama_alive():
        ok('服务已在运行')
        return True
    exe = find_ollama()
    if not exe:
        err('未找到 Ollama 可执行文件')
        return False
    kw = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if platform.system() == 'Windows':
        kw['creationflags'] = 0x00000008 | 0x00000200
    else:
        kw['start_new_session'] = True
    subprocess.Popen([str(exe), 'serve'], **kw)
    for _ in range(20):
        time.sleep(1)
        if _ollama_alive():
            ok('服务已就绪')
            return True
    err('启动超时')
    return False


def _ollama_alive():
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',
                                    timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def pull_model():
    step(f'下载 embedding 模型 {EMBED_MODEL}（约 1.2 GB）')
    exe = find_ollama()
    if not exe:
        err('未找到 Ollama')
        return False
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',
                                    timeout=5) as r:
            models = [m.get('name', '') for m in json.loads(r.read()).get('models', [])]
        if any(m.startswith(EMBED_MODEL) for m in models):
            ok(f'{EMBED_MODEL} 已存在，跳过')
            return True
    except Exception:
        pass

    proc = subprocess.Popen([str(exe), 'pull', EMBED_MODEL])
    proc.wait()
    if proc.returncode == 0:
        ok('模型下载完成')
        return True
    err('模型下载失败')
    return False


# ---------- 3. 配置 ----------

def configure_key():
    step('配置 MiMo API Key')
    from custom.gui.backend import read_mimo_key, save_mimo_key, test_mimo

    if read_mimo_key():
        ok('已配置过，如需更换请重新输入')
    print()
    print('  大模型 API Key 获取方式（任选一家）：')
    print()
    print('  【MiMo 小米】推荐，注册即送体验金')
    print('    1. 打开 https://platform.xiaomimimo.com?ref=XSGMF9')
    print('    2. 用邀请码 XSGMF9 注册（双方各得 ¥10 体验金，首单 9 折）')
    print('    3. 创建 API Key，复制以 sk- 开头的字符串')
    print()
    print('  【DeepSeek】性价比高，中文强')
    print('    1. 打开 https://platform.deepseek.com/api_keys')
    print('    2. 注册并创建 API Key')
    print()

    key = input('  请粘贴 API Key（直接回车跳过）: ').strip()
    if not key:
        warn('已跳过，稍后可在桌面程序里配置')
        return False

    print('  正在保存并测试连接...')
    r = save_mimo_key(key, 'mimo-v2.6-flash')
    if not r.get('ok'):
        err(r.get('msg', '保存失败'))
        return False
    t = test_mimo(key, 'mimo-v2.6-flash')
    if t.get('ok'):
        ok(t.get('msg', '连接正常'))
        return True
    err(t.get('msg', '连接失败'))
    return False


# ---------- 主流程 ----------

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只检测不安装')
    args = ap.parse_args()

    title('DeepTutor 试卷助手 · 环境引导')
    print(f'  系统: {platform.system()} {platform.release()}')
    print(f'  目录: {ROOT}')

    if not check_python() or not check_deeptutor():
        sys.exit(1)

    if args.check:
        step('当前状态')
        exe = find_ollama()
        print(f'  Ollama      : {"已安装 " + str(exe) if exe else "未安装"}')
        print(f'  Ollama 服务 : {"运行中" if _ollama_alive() else "未启动"}')
        print(f'  {EMBED_MODEL:<12}: ', end='')
        try:
            with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',
                                        timeout=3) as r:
                ms = [m['name'] for m in json.loads(r.read()).get('models', [])]
            print('已就绪' if any(m.startswith(EMBED_MODEL) for m in ms) else '未下载')
        except Exception:
            print('无法检测')
        try:
            from custom.gui.backend import read_mimo_key
            k = read_mimo_key()
            print(f'  MiMo Key    : {"已配置" if k else "未配置"}')
        except Exception:
            pass
        return

    # 1. Ollama
    if find_ollama():
        ok('Ollama 已安装')
    else:
        if not install_ollama():
            print('\n安装未完成，请按上面的提示处理后重新运行本脚本。')
            sys.exit(1)
        if not find_ollama():
            warn('刚安装完，可能需要重开终端才能识别。请重新运行本脚本。')
            sys.exit(0)

    # 2. 服务 + 模型
    if not start_ollama():
        sys.exit(1)
    if not pull_model():
        sys.exit(1)

    # 3. Key
    configure_key()

    title('全部就绪')
    print('  接下来可以：')
    print('    · 启动桌面程序： python custom/gui/app.py')
    print('    · 批量导入试卷： python custom/importer/batch.py --list')
    print()


if __name__ == '__main__':
    main()
