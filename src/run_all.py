# -*- coding: utf-8 -*-
"""一键复现:从源文件到成品(视频+图片)+ 验收数据。
前置: 项目运行时已就绪(见 README 快速开始;ffmpeg 按 common 的可移植性约定解析);
      代理已按需求书配置。
用法: python src/run_all.py            # 全流程
       python src/run_all.py --skip-embed   # 跳过嵌入(成品已存在),只做验收
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
PY = sys.executable


def sh(cmd):
    print(f"[run_all] $ {cmd}", flush=True)
    rc = subprocess.call(cmd, cwd=str(PROJ))
    assert rc == 0, f"失败: {cmd}"


def latest_product():
    """取本次验收的成品:优先 成品_meta.json 登记的 out(与嵌入步骤同源),回退到最新 mtime 的成品。"""
    meta_f = PROJ / "output" / "成品_meta.json"
    if meta_f.exists():
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        out = Path(meta.get("out", ""))
        if out.exists():
            return out
    cands = sorted(PROJ.glob("output/*_已加水印*.mp4"), key=lambda q: q.stat().st_mtime)
    if not cands:
        raise SystemExit("[run_all] output\\ 下没有成品;请先运行嵌入(或检查 output/成品_meta.json)")
    return cands[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-embed", action="store_true")
    args = ap.parse_args()

    if not args.skip_embed:
        sh(f'"{PY}" src/embed_video.py --crf 14')

    # 先嵌入后取成品,验收针对本次新成品(旧实现先取后嵌,output 为空时还会直接崩溃)
    product = latest_product()

    # 图片成品(直接运行脚本;src 目录在 sys.path 上,embed_images.py 可正常 import common)
    sh(f'"{PY}" src/embed_images.py')

    # 验收: 两段全项
    sh(f'"{PY}" src/verify.py --product "{product}" --start 5000')
    sh(f'"{PY}" src/verify.py --product "{product}" --start 17800')

    # 工具抽检
    sh(f'"{PY}" tools/extract_wm.py video "{product}" --t 100')
    print("[run_all] 全流程完成", flush=True)


if __name__ == "__main__":
    main()
