# -*- coding: utf-8 -*-
"""一键复现:从源文件到成品(视频+图片)+ 验收数据。
前置: conda activate wm2;代理已按需求书配置;ffmpeg 在 PATH 或使用默认路径。
用法:  python src/run_all.py            # 全流程
       python src/run_all.py --skip-embed   # 跳过嵌入(成品已存在),只做验收
"""
import argparse
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
PY = sys.executable


def sh(cmd):
    print(f"[run_all] $ {cmd}", flush=True)
    rc = subprocess.call(cmd, cwd=str(PROJ))
    assert rc == 0, f"失败: {cmd}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-embed", action="store_true")
    args = ap.parse_args()

    product = max(PROJ.glob("output/*_已加水印*.mp4"), key=lambda q: q.stat().st_mtime)
    if not args.skip_embed:
        sh(f'"{PY}" src/embed_video.py --crf 14')

    # 图片成品(直接调 exp 逻辑太重,这里独立内联)
    sh(f'"{PY}" -c "import sys; sys.path.insert(0, r\'{PROJ / "exp"}\'); '
       f"exec(open(r'{PROJ / 'src' / 'embed_images.py'}', encoding='utf-8').read())\"")

    # 验收: 两段全项
    sh(f'"{PY}" src/verify.py --product "{product}" --start 5000')
    sh(f'"{PY}" src/verify.py --product "{product}" --start 17800')

    # 工具抽检
    sh(f'"{PY}" tools/extract_wm.py video "{product}" --t 100')
    print("[run_all] 全流程完成", flush=True)


if __name__ == "__main__":
    main()
