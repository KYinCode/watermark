# -*- coding: utf-8 -*-
"""WebUI 嵌入子进程(R6.1/R6.3):参数化作品文本 -> 派生 ID -> 嵌入。

用法:
  视频: python webui/engine_embed.py --input <视频> --text "版权文本" --scaling-w 2.0 --crf 14 [--comp 0.4]
  图片: python webui/engine_embed.py --images a.png b.png --text "版权文本" --scaling-w 2.5

stdout 按行输出 JSON 事件(后端逐行解析):
  {"type":"part","path":...}                  半成品路径(失败/取消时由后端清理)
  {"type":"start","kind":..,"input":..,"total":..,"w":..,"h":..,"fps":..}
  {"type":"progress","done":..,"total":..}
  {"type":"psnr","mean":..,"min":..,"p5":..}
  {"type":"selfcheck","hit":..,"acc":..,"index":0}
  {"type":"item","index":..,"total":..,"input":..,"out":..,"psnr":..}   (图片批量逐张)
  {"type":"done","out":..,"out_meta":..,"size_mb":..,"frames":..}
  {"type":"error","message":..}
失败/取消不产生半成品: 成品先写 output/.part-*,成功后原子改名(R2.8/R2.9)。
成品命名 <源名>_已加水印.mp4/.png,重名自动加 _v2/_v3… 尾缀;meta JSON 同名 _meta。
视频嵌入核心(流式管道/色彩回补/帧数校验/自检)统一在 common.embed_video,本脚本只做协议与落盘编排。
"""
import argparse
import contextlib
import json
import logging
import sys
import time
import uuid
from pathlib import Path

import torch

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ / "src"))
import common as C
import wmlog

wmlog.setup_engine()  # 引擎日志只走 stderr(后端已把它接进任务日志);stdout 是 JSON 协议通道
log = logging.getLogger("wm.embed")


def emit(obj: dict):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def embed_one_image(wam, msg1, expect_bits, img_path: Path, part: Path):
    import cv2
    img = cv2.cvtColor(C.imread_unicode(img_path), cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    with torch.no_grad():
        out = wam.embed(C.norm_frames(img[None]), msg1)
    wm = C.unnorm_to_uint8(out["imgs_w"])[0]
    C.imwrite_unicode(part, cv2.cvtColor(wm, cv2.COLOR_RGB2BGR))
    psnr = round(C.psnr_db(img, wm), 2)
    # 自检: 回读已写文件直解
    back = cv2.cvtColor(C.imread_unicode(part), cv2.COLOR_BGR2RGB)
    accs, _, _ = C.decode_batch_stats(wam, [back], 1, expect_bits, report_ms=False)
    sc = dict(hit=bool(accs[0] == 1.0), acc=round(float(accs[0]), 4))
    return dict(psnr=psnr, w=w, h=h, selfcheck=sc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=str, default=None, help="源视频路径")
    ap.add_argument("--images", nargs="+", type=str, default=None, help="源图片路径(批量)")
    ap.add_argument("--text", type=str, required=True, help="作品版权文本(派生 ID)")
    ap.add_argument("--work-name", type=str, default="")
    ap.add_argument("--scaling-w", type=float, default=2.0)
    ap.add_argument("--crf", type=int, default=14)
    ap.add_argument("--comp", type=float, default=-1.0,
                    help="视频色彩回补:嵌入前红绿各预减 N 级,抵消泛黄/泛紫;"
                         "<0=按强度自动配量(1.5→0.4,2.0→0.5,强度每+0.5约+0.1),0=关闭;图片路径恒不补偿")
    args = ap.parse_args()
    C.OUTPUT.mkdir(exist_ok=True)
    if args.input:
        comp = round(args.comp if args.comp >= 0
                     else min(0.8, max(0.3, 0.4 + 0.2 * (args.scaling_w - 1.5))), 2)
    else:
        comp = 0.0  # 图片无损链路实测白区色差仅 -0.04~-0.10 级,无需回补

    part = None
    try:
        # redirect_stdout:WAM/omegaconf 加载期的杂散 print 走 stderr,不污染 JSON 协议
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(sys.stderr):
            wam, msg1, expect_bits, expect_id = C.load_payload(args.text, args.scaling_w)
        log.info("模型加载完成,耗时 %.1fs(sw=%s crf=%s comp=%s)",
                 time.perf_counter() - t0, args.scaling_w, args.crf, comp)
        id_hex = f"{expect_id:08x}"

        if args.input:
            src = Path(args.input)
            if not src.exists():
                raise FileNotFoundError(f"源文件不存在: {src}")
            final = C.out_name(src.stem, ".mp4")
            part = C.OUTPUT / f"{C.PART_PREFIX}{uuid.uuid4().hex[:8]}-{final.name}"
            emit(dict(type="part", path=str(part), final=str(final)))
            log.info("开始嵌入视频 %s -> %s", src, final)
            info = C.embed_video(wam, msg1, expect_bits, src, args.crf, part, comp, emit=emit)
            part.replace(final)
            log.info("视频嵌入完成:%s 帧,PSNR mean=%.2f,耗时 %.0fs",
                     info["frames"], info["psnr"]["mean"], info["wall_s"])
            meta = dict(source=str(src), out=str(final), work=args.work_name, text=args.text,
                        id_hex=id_hex, scaling_w=args.scaling_w, crf=args.crf, comp=comp,
                        created_at=time.strftime("%Y-%m-%d %H:%M:%S"), **info)
            meta_path = final.with_name(final.stem + "_meta.json")
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            emit(dict(type="done", out=str(final), out_meta=str(meta_path),
                      size_mb=round(final.stat().st_size / 2**20, 1), **{k: info[k] for k in ("frames",)}))
        else:
            imgs = [Path(p) for p in (args.images or [])]
            if not imgs:
                raise ValueError("未提供素材")
            for p in imgs:
                if not p.exists():
                    raise FileNotFoundError(f"源文件不存在: {p}")
            log.info("开始嵌入图片 %d 张", len(imgs))
            emit(dict(type="start", kind="images", input=",".join(p.name for p in imgs[:3]) +
                      ("…" if len(imgs) > 3 else ""), total=len(imgs)))
            items = []
            for i, p in enumerate(imgs):
                final = C.out_name(p.stem, ".png")
                part = C.OUTPUT / f"{C.PART_PREFIX}{uuid.uuid4().hex[:8]}-{final.name}"
                info = embed_one_image(wam, msg1, expect_bits, p, part)
                part.replace(final)
                meta_path = final.with_name(final.stem + "_meta.json")
                meta_path.write_text(json.dumps(
                    dict(source=str(p), out=str(final), work=args.work_name, text=args.text,
                         id_hex=id_hex, scaling_w=args.scaling_w,
                         created_at=time.strftime("%Y-%m-%d %H:%M:%S"), **info),
                    ensure_ascii=False, indent=2), encoding="utf-8")
                sc = info.pop("selfcheck")
                emit(dict(type="selfcheck", index=i, **sc))
                emit(dict(type="item", index=i, total=len(imgs), input=p.name,
                          out=str(final), **info))
                items.append(dict(input=p.name, out=str(final), **info))
                emit(dict(type="progress", done=i + 1, total=len(imgs)))
                part = None
            emit(dict(type="done", out=items[0]["out"], items=items))
    except Exception as e:
        log.exception("嵌入失败")
        emit(dict(type="error", message=f"{type(e).__name__}: {e}"))
        if part is not None and part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        sys.exit(1)
    finally:
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass


if __name__ == "__main__":
    main()
