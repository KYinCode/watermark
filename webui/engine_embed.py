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
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import torch

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ / "src"))
import common as C

PART_PREFIX = ".part-"


def emit(obj: dict):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def out_name(stem: str, ext: str) -> Path:
    """<源名>_已加水印<ext>,重名自动 _v2.._v99"""
    cand = C.OUTPUT / f"{stem}_已加水印{ext}"
    k = 2
    while cand.exists():
        cand = C.OUTPUT / f"{stem}_已加水印_v{k}{ext}"
        k += 1
        if k > 99:
            raise RuntimeError("成品重名过多,请清理 output\\")
    return cand


def read_exact(stream, nbytes):
    buf = bytearray()
    while len(buf) < nbytes:
        chunk = stream.read(nbytes - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf) if buf else None


def selfcheck_frame(wam, product_path, expect_bits, frame_no, w, h):
    """对成品抽 1 帧直解自检(R2.6)"""
    frames = C.read_frames(product_path, frame_no, 1, w=w, h=h)
    if not frames:
        return dict(hit=False, acc=0.0, note="抽帧失败")
    accs, _, _ = C.decode_batch_stats(wam, frames, 1, expect_bits, report_ms=False)
    return dict(hit=bool(accs[0] == 1.0), acc=round(float(accs[0]), 4))


def embed_video(wam, msg1, expect_bits, src: Path, crf: int, part: Path, comp: float = 0.0):
    fps, w, h, total = C.probe(src)
    emit(dict(type="start", kind="video", input=src.name, total=total, w=w, h=h, fps=round(fps, 3)))
    mean_t = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std_t = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)

    dec = subprocess.Popen(
        [C.FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src),
         "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-vf", "scale=in_color_matrix=bt709", "-"],
        stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    enc = subprocess.Popen(
        [C.FF, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "-",
         "-i", str(src),
         "-map", "0:v", "-map", "1:a?",
         "-vf", "scale=out_color_matrix=bt709,format=yuv420p,"
                "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-c:a", "copy", str(part)],
        stdin=subprocess.PIPE)

    B = 8
    frame_bytes = w * h * 3
    psnr_samples = []
    n_done = 0
    t0 = time.perf_counter()
    try:
        while True:
            raw = read_exact(dec.stdout, frame_bytes * B)
            if raw is None:
                break
            batch = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)
            if comp:  # 色彩回补:嵌入前红绿各预减 N 级(等效蓝差回补;白底 B=255 顶格不能直接加蓝)。PSNR 对未补偿源
                f = batch.astype(np.float32)
                f[:, :, :, 0] = np.clip(f[:, :, :, 0] - comp, 0, 255)
                f[:, :, :, 1] = np.clip(f[:, :, :, 1] - comp, 0, 255)
                x = C.norm_frames(f)
                base = torch.from_numpy(batch).cuda().permute(0, 3, 1, 2).float() / 255.0
            else:
                x = C.norm_frames(batch)
                base = x * std_t + mean_t
            with torch.no_grad():
                out = wam.embed(x, msg1.repeat(x.shape[0], 1))
            y8 = (out["imgs_w"] * std_t + mean_t).clamp(0, 1).mul(255).round().byte()
            yf = y8.float() / 255.0
            mse = ((yf - base) ** 2).mean(dim=(1, 2, 3))
            psnr_samples.append((-10 * torch.log10(mse + 1e-12)).cpu().numpy())
            wm = y8.permute(0, 2, 3, 1).cpu().numpy()
            enc.stdin.write(wm.tobytes())
            n_done += len(batch)
            emit(dict(type="progress", done=n_done, total=total or n_done))
            last = len(batch) < B
            del x, base, out, y8, yf, mse, wm, batch
            if last:
                break
    finally:
        try:
            enc.stdin.close()
        except Exception:
            pass
        try:
            dec.stdout.close()
        except Exception:
            pass
        rc_dec = dec.wait()
        rc_enc = enc.wait()
    if rc_enc != 0:
        raise RuntimeError(f"编码失败 rc={rc_enc} rc_dec={rc_dec}")

    wall = time.perf_counter() - t0
    psnrs = np.concatenate(psnr_samples)
    psnr_stat = dict(mean=round(float(psnrs.mean()), 2), min=round(float(psnrs.min()), 2),
                     p5=round(float(np.percentile(psnrs, 5)), 2))
    emit(dict(type="psnr", **psnr_stat))

    # 帧数校验
    pr = subprocess.run(
        [C.FF.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error", "-count_frames",
         "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "json",
         str(part)], capture_output=True, text=True).stdout
    nb = int(json.loads(pr)["streams"][0]["nb_read_frames"])
    if nb != n_done:
        raise RuntimeError(f"帧数校验失败: 成品 {nb} 帧 != 嵌入 {n_done} 帧")

    # 自检: 中段抽 1 帧直解(R2.6)
    sc = selfcheck_frame(wam, part, expect_bits, n_done // 2, w, h)
    emit(dict(type="selfcheck", index=0, **sc))
    return dict(frames=n_done, psnr=psnr_stat, wall_s=round(wall, 1), w=w, h=h, fps=fps)


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
    expect_id = C.derive_id(args.text)
    expect_bits = C.text_to_bits(args.text)
    C.OUTPUT.mkdir(exist_ok=True)
    if args.input:
        comp = round(args.comp if args.comp >= 0
                     else min(0.8, max(0.3, 0.4 + 0.2 * (args.scaling_w - 1.5))), 2)
    else:
        comp = 0.0  # 图片无损链路实测白区色差仅 -0.04~-0.10 级,无需回补

    part = None
    try:
        wam = C.load_wam(scaling_w=args.scaling_w)
        msg1 = torch.from_numpy(expect_bits).float().unsqueeze(0).cuda()
        id_hex = f"{expect_id:08x}"

        if args.input:
            src = Path(args.input)
            if not src.exists():
                raise FileNotFoundError(f"源文件不存在: {src}")
            final = out_name(src.stem, ".mp4")
            part = C.OUTPUT / f"{PART_PREFIX}{uuid.uuid4().hex[:8]}-{final.name}"
            emit(dict(type="part", path=str(part), final=str(final)))
            info = embed_video(wam, msg1, expect_bits, src, args.crf, part, comp)
            part.replace(final)
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
            emit(dict(type="start", kind="images", input=",".join(p.name for p in imgs[:3]) +
                      ("…" if len(imgs) > 3 else ""), total=len(imgs)))
            items = []
            for i, p in enumerate(imgs):
                final = out_name(p.stem, ".png")
                part = C.OUTPUT / f"{PART_PREFIX}{uuid.uuid4().hex[:8]}-{final.name}"
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
