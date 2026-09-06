# -*- coding: utf-8 -*-
"""全片嵌入管线(CLI 适配层)。核心嵌入逻辑统一在 common.embed_video(引擎版为基准,唯一实现):
ffmpeg 流式管道、逐帧 WAM 嵌入、色彩回补、PSNR、帧数校验、成品抽帧自检、.part-* 半成品 + 原子改名。
本脚本只负责两件事:把核心事件翻译成人类可读输出;写验收约定 output/成品_meta.json(run_all/make_report 依赖)。

用法:
  python src/embed_video.py                                  # data\ 下唯一视频,默认参数
  python src/embed_video.py --input data/xxx.mp4 --crf 14 --scaling-w 2.0 --comp 0.4 --out output/y.mp4
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "src"))
import common as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=str, default=None, help="源视频;默认取 data\ 下唯一视频")
    ap.add_argument("--text", type=str, default=C.WM_TEXT, help="作品版权文本(派生 ID);默认项目主文本")
    ap.add_argument("--crf", type=int, default=14)
    ap.add_argument("--scaling-w", type=float, default=2.0, help="嵌入强度;默认 2.0")
    ap.add_argument("--comp", type=float, default=-1.0,
                    help="色彩回补:嵌入前红绿各预减 N 级,抵消泛黄/泛紫;"
                         "<0=按强度自动配量(1.5→0.4,2.0→0.5,强度每+0.5约+0.1),0=关闭")
    ap.add_argument("--out", type=str, default=None, help="成品路径;默认 output\<源名>_已加水印[_v2].mp4")
    args = ap.parse_args()

    src = Path(args.input) if args.input else C.one_video()
    if not src.exists():
        raise SystemExit(f"源文件不存在: {src}")
    final = Path(args.out) if args.out else C.out_name(src.stem, ".mp4")
    C.OUTPUT.mkdir(exist_ok=True)
    part = C.OUTPUT / f"{C.PART_PREFIX}{uuid.uuid4().hex[:8]}-{final.name}"

    wam, msg1, expect_bits, expect_id = C.load_payload(args.text, args.scaling_w)
    comp = round(args.comp if args.comp >= 0
                 else min(0.8, max(0.3, 0.4 + 0.2 * (args.scaling_w - 1.5))), 2)
    print(f"[embed] 源: {src.name}  强度 scaling_w={args.scaling_w}  色彩回补 comp={comp}"
          f"{'(自动)' if args.comp < 0 else ''}", flush=True)

    meta = {}
    def cli_emit(ev: dict):
        """核心事件 -> 人类可读输出 + 收集验收 meta 字段"""
        t = ev.get("type")
        if t == "progress":
            done, total = ev.get("done", 0), ev.get("total")
            if done % 1600 < 8:
                print(f"  进度 {done}/{total or '?'} 帧", flush=True)
        elif t == "psnr":
            print(f"[embed] PSNR mean={ev['mean']:.2f} min={ev['min']:.2f} p5={ev['p5']:.2f}", flush=True)
            meta.update(psnr_mean=ev["mean"], psnr_min=ev["min"], psnr_p5=ev["p5"])
        elif t == "selfcheck":
            print(f"[自检] 中段抽帧直解: {'✓' if ev['hit'] else '✗'} acc={ev['acc']}", flush=True)
            meta["selfcheck"] = ev
        elif t == "part":
            print(f"[embed] 半成品 {ev['path']} -> {ev['final']}", flush=True)

    try:
        info = C.embed_video(wam, msg1, expect_bits, src, args.crf, part, comp, emit=cli_emit)
        part.replace(final)
    except Exception:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        raise

    meta.update(source=str(src), out=str(final), frames=info["frames"], crf=args.crf,
                wall_s=info["wall_s"], embed_ms=info["embed_ms"],
                size_mb=round(final.stat().st_size / 2**20, 1),
                scaling_w=args.scaling_w, comp=comp, id_hex=f"{expect_id:08x}")
    (C.OUTPUT / "成品_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[embed] 完成 {info['frames']} 帧, 总耗时 {info['wall_s']}s "
          f"(嵌入纯GPU {info['embed_ms']:.1f} ms/帧) -> {final}", flush=True)
    print("[embed] 全部完成", flush=True)


if __name__ == "__main__":
    main()
