# -*- coding: utf-8 -*-
"""试卷助手 Web 服务 —— 把桌面版的功能搬到浏览器里。

容器部署时用这个:官方 DeepTutor 跑在 3782,本服务跑在 8002,
两者共用同一份数据目录与知识库。

启动:
  python custom/web/server.py --port 8002

依赖:仅用标准库(HTTP + JSON),不需要额外安装。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.winutil import no_window_kwargs  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent / 'static'

# 进度缓存：前端轮询这个来显示实时日志
LOG_BUFFER: list[dict] = []
LOG_LOCK = threading.Lock()


def emit(kind: str, msg: str):
    with LOG_LOCK:
        LOG_BUFFER.append({'kind': kind, 'msg': str(msg)})
        if len(LOG_BUFFER) > 500:
            del LOG_BUFFER[:200]


def bg(fn):
    def runner():
        try:
            fn()
        except Exception as e:
            emit('error', f'{e}\n{traceback.format_exc()[:400]}')
    threading.Thread(target=runner, daemon=True).start()


# ---------- 业务处理 ----------

def handle_api(path: str, payload: dict) -> dict:
    """路由到对应的处理函数。"""
    from custom.gui import backend as B

    if path == '/api/env':
        return B.check_env()

    if path == '/api/providers':
        return {k: {'name': v['name'], 'models': v['models'],
                    'default': v['default'], 'key_url': v['key_url'],
                    'key_label': v.get('key_label', ''),
                    'key_hint': v['key_hint'], 'base_url': v['base_url']}
                for k, v in B.LLM_PROVIDERS.items()}

    if path == '/api/save_key':
        key = payload.get('api_key', '')
        provider = payload.get('provider', 'xiaomi_mimo')
        model = payload.get('model', '')
        base_url = payload.get('base_url', '')
        res = B.save_llm_config(key, provider, model, base_url)
        if res.get('ok'):
            res['test'] = B.test_llm(key, provider, model, base_url)
        return res

    if path == '/api/deploy':
        def job():
            emit('info', '=== 启动 Ollama ===')
            if not B.start_ollama(emit):
                emit('error', 'Ollama 启动失败')
                return
            emit('info', '=== 下载 embedding 模型 ===')
            ok = B.pull_embed_model(emit)
            emit('done' if ok else 'error',
                 '本地模型部署完成' if ok else '模型下载失败')
        bg(job)
        return {'ok': True, 'msg': '任务已启动'}

    if path == '/api/scan':
        from custom.vision import count_images, estimate_cost
        d = Path(payload.get('dir', ''))
        if not d.is_dir():
            return {'ok': False, 'msg': f'目录不存在: {d}'}
        items = []
        for md in sorted(d.rglob('*.md')):
            if md.name.endswith('.enriched.md'):
                continue
            _, uniq = count_images(md)
            if uniq:
                est = estimate_cost(md)
                items.append({'path': str(md), 'name': md.name,
                              'images': uniq, 'cost': est['cost_yuan']})
        return {'ok': True, 'items': items,
                'total_cost': round(sum(i['cost'] for i in items), 2)}

    if path == '/api/enhance':
        from custom.vision import enhance_markdown
        paths = payload.get('paths', [])
        key = payload.get('api_key', '')
        model = payload.get('model', 'mimo-v2.6-flash')

        def job():
            total = len(paths)
            for i, p in enumerate(paths, 1):
                emit('info', f'--- [{i}/{total}] {Path(p).name} ---')
                r = enhance_markdown(Path(p), key, model, workers=2,
                                     on_progress=emit)
                if not r.get('ok'):
                    emit('error', r.get('msg', '失败'))
            emit('done', f'视觉增强完成（{total} 个文件）')
        bg(job)
        return {'ok': True, 'msg': '任务已启动'}

    if path == '/api/kbs':
        from custom.importer.core import list_kbs
        try:
            return {'ok': True, 'kbs': list_kbs()}
        except Exception as e:
            return {'ok': False, 'msg': str(e), 'kbs': []}

    if path == '/api/import':
        from custom.importer.core import import_paths_sync, scan_files
        kb = payload.get('kb', '')
        d = payload.get('dir', '')
        use_enriched = payload.get('use_enriched', True)

        def job():
            files = scan_files(d)
            if use_enriched:
                picked, seen = [], set()
                for f in files:
                    if f.suffix == '.md':
                        en = f.with_suffix('.enriched.md')
                        if en.exists():
                            picked.append(en)
                            seen.add(f.stem)
                            continue
                    if f.stem in seen:
                        continue
                    picked.append(f)
                files = picked
            total = len(files)
            emit('info', f'共 {total} 个文件待导入')
            for i in range(0, total, 20):
                chunk = files[i:i + 20]
                emit('info', f'导入 {i+1}-{min(i+20, total)}/{total} ...')
                import_paths_sync(kb, chunk)
            emit('done', f'导入完成（{total} 个文件）')
        bg(job)
        return {'ok': True, 'msg': '任务已启动'}

    if path == '/api/generate':
        import subprocess
        kb = payload.get('kb', '')
        topic = payload.get('topic', '')
        count = payload.get('count', 5)
        diff = payload.get('difficulty', '')

        def job():
            emit('info', f'正在基于「{kb}」出题：{topic}')
            cmd = [sys.executable, '-m', 'deeptutor_cli.main', 'run',
                   'deep_question', topic, '--kb', kb,
                   '--config', f'num_questions={count}']
            if diff:
                cmd += ['--config', f'difficulty={diff}']
            try:
                proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                                      text=True, encoding='utf-8',
                                      errors='replace', timeout=900,
                                      **no_window_kwargs())
                out = (proc.stdout or '')[-5000:] or (proc.stderr or '')[-2000:]
                emit('result', out)
            except Exception as e:
                emit('error', f'出题失败: {e}')
        bg(job)
        return {'ok': True, 'msg': '任务已启动'}

    if path == '/api/logs':
        since = int(payload.get('since', 0))
        with LOG_LOCK:
            return {'ok': True, 'logs': LOG_BUFFER[since:],
                    'total': len(LOG_BUFFER)}

    return {'ok': False, 'msg': f'未知接口: {path}'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass          # 静音访问日志

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            path = '/index.html'
        f = WEB_DIR / path.lstrip('/')
        if f.is_file():
            data = f.read_bytes()
            ctype = mimetypes.guess_type(str(f))[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype == 'application/javascript':
                ctype += '; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json({'ok': False, 'msg': 'not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            payload = {}
        try:
            self._json(handle_api(path, payload))
        except Exception as e:
            self._json({'ok': False, 'msg': str(e),
                        'trace': traceback.format_exc()[:600]}, 500)


def main():
    ap = argparse.ArgumentParser(description='试卷助手 Web 服务')
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=8002)
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'试卷助手已启动: http://{args.host}:{args.port}')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == '__main__':
    main()
