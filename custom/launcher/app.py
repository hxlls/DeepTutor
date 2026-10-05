# -*- coding: utf-8 -*-
"""DeepTutor 桌面启动器（pywebview 原生窗口）。

自动拉起后端与前端，等就绪后开原生窗口；关窗时清理全部子进程。
不修改 DeepTutor 任何核心文件。

用法:
  python3 custom/launcher/app.py
  python3 custom/launcher/app.py --port 3782 --backend-port 8001
  python3 custom/launcher/app.py --no-frontend    # 前端已 build 过时跳过 dev server

依赖:
  pip install pywebview
  Windows 需 WebView2 运行时（Win10 1809+/Win11 通常已预装）
"""
import argparse
import atexit
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _popen(cmd, cwd, env):
    return subprocess.Popen(
        cmd, cwd=str(cwd), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def start_backend(python, port):
    env = os.environ.copy()
    env['BACKEND_PORT'] = str(port)
    return _popen([python, '-m', 'deeptutor.api.run_server'], ROOT, env)


def start_frontend(port, backend_port):
    env = os.environ.copy()
    env['NEXT_PUBLIC_API_BASE_EXTERNAL'] = f'http://127.0.0.1:{backend_port}'
    npm = 'npm.cmd' if os.name == 'nt' else 'npm'
    return _popen([npm, 'run', 'dev', '--', '-p', str(port)], ROOT / 'web', env)


def wait_for(url, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except Exception:
            time.sleep(1.5)
    return False


def main():
    ap = argparse.ArgumentParser(description='DeepTutor 桌面启动器')
    ap.add_argument('--port', type=int, default=3782, help='前端端口')
    ap.add_argument('--backend-port', type=int, default=8001, help='后端端口')
    ap.add_argument('--python', default=sys.executable, help='用于跑后端的 Python')
    ap.add_argument('--no-frontend', action='store_true',
                    help='跳过前端 dev server（前端已构建时用）')
    ap.add_argument('--timeout', type=int, default=240, help='就绪等待上限秒数')
    args = ap.parse_args()

    procs = []

    def cleanup(*_):
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in procs:
            try:
                p.wait(timeout=6)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    atexit.register(cleanup)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *a: sys.exit(0))
        except Exception:
            pass

    print(f'仓库根目录: {ROOT}')
    print(f'启动后端 (:{args.backend_port}) ...')
    procs.append(start_backend(args.python, args.backend_port))

    if not args.no_frontend:
        print(f'启动前端 (:{args.port}) ...')
        procs.append(start_frontend(args.port, args.backend_port))

    url = f'http://127.0.0.1:{args.port}'
    print(f'等待服务就绪: {url}（最多 {args.timeout}s）')
    if not wait_for(url, args.timeout):
        print('超时：服务未就绪，请检查前后端日志。')
        cleanup()
        sys.exit(1)

    print('服务就绪，打开窗口...')
    try:
        import webview
    except ImportError:
        print('缺少 pywebview，请先执行: pip install pywebview')
        print(f'（服务已在运行，可直接浏览器访问 {url}）')
        input('按回车结束并清理进程...')
        cleanup()
        return

    webview.create_window('DeepTutor', url, width=1440, height=920,
                          min_size=(1024, 700))
    webview.start()

    print('窗口已关闭，清理子进程...')
    cleanup()


if __name__ == '__main__':
    main()
