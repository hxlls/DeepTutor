# -*- coding: utf-8 -*-
"""测试 MiMo 视觉输入：给一张图，让它输出 LaTeX 或描述。

用法:
  python3 test_mimo_vision.py <图片路径>
"""
import base64
import json
import sys
import urllib.request
from pathlib import Path

KEY = 'sk-cila3d49dhb85dtduskpotb0zmpvwshxy71595wmrl0dcqvv'
URL = 'https://api.xiaomimimo.com/v1/chat/completions'
MODEL = 'mimo-v2.6-flash'

PROMPT = """这是一份中国中学试卷中的图片。请判断它的内容：
- 如果是数学公式或表达式：输出 LaTeX（行内 $...$，独立公式 $$...$$）
- 如果是几何图形/函数图像：用一句话说明图形类型、标注字母与已知条件
- 如果是装饰、水印、logo：输出 EMPTY
只输出结果本身，不要解释。"""

MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv) < 2:
        print(__doc__)
        return

    p = Path(sys.argv[1])
    mime = MIME.get(p.suffix.lower())
    if not mime:
        print(f'不支持的格式: {p.suffix}')
        return

    size_kb = p.stat().st_size / 1024
    b64 = base64.b64encode(p.read_bytes()).decode()
    print(f'图片: {p.name}  ({size_kb:.1f} KB)')

    payload = {
        'model': MODEL,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': PROMPT},
                {'type': 'image_url',
                 'image_url': {'url': f'data:{mime};base64,{b64}'}},
            ],
        }],
        'max_tokens': 400,
    }

    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {KEY}',
                 'Content-Type': 'application/json'},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
        msg = resp['choices'][0]['message']
        print(f"\n识别结果:\n{msg.get('content', '')}")
        if msg.get('reasoning_content'):
            print(f"\n(推理: {msg['reasoning_content'][:150]}...)")
    except Exception as e:
        print(f'失败: {e}')


if __name__ == '__main__':
    main()
