# -*- coding: utf-8 -*-
"""全片嵌入管线:源视频 -> 逐帧 WAM 嵌入 -> 成品 mp4(crf14, 音轨 copy)。
流式处理,显存/内存恒定;结尾 ffprobe 校验帧数。
用法: python pipeline_embed_video.py [--crf 14]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch

import common as C


def read_exact(stream, nbytes):
    """读满 nbytes;EOF 时返回已有部分(可能不足),完全无数据返回 None"""
    buf = bytearray()
    while len(buf) < nbytes:
        chunk = stream.read(nbytes - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    if not buf:
        return None
    return bytes(buf)


def out_name(stem: str, ext: str) -> Path:
    """<源名>_已加水印<ext>,重名自动 _v2.._v99(与引擎版一致)"""
    cand = C.OUTPUT / f"{stem}_已加水印{ext}"
    k = 2
    while cand.exists():
        cand = C.OUTPUT / f"{stem}_已加水印_v{k}{ext}"
        k += 1
        if k > 99:
            raise RuntimeError("成品重名过多,请清理 output\\")
    return cand


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=str, default=None, help="源视频;默认取 data\ 下唯一视频")
    ap.add_argument("--crf", type=int, default=14)
    ap.add_argument("--scaling-w", type=float, default=2.0, help="嵌入强度;默认 2.0")
    ap.add_argument("--comp", type=float, default=-1.0,
                    help="色彩回补:嵌入前红绿各预减 N 级,抵消泛黄/泛紫;"
                         "<0=按强度自动配量(1.5→0.4,2.0→0.5,强度每+0.5约+0.1),0=关闭")
    ap.add_argument("--out", type=str, default=None, help="成品路径;默认 output\<源名>_已加水印.mp4")
    args = ap.parse_args()

    src = Path(args.input) if args.input else C.one_video()
    fps, w, h, total_frames = C.probe(src)
    out_path = Path(args.out) if args.out else out_name(src.stem, ".mp4")
    C.OUTPUT.mkdir(exist_ok=True)
    print(f"[embed] 源: {src.name}  {w}x{h}@{fps:.0f}fps  帧数: {total_frames or '未知'}", flush=True)
    B = 8
    mean_t = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std_t = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)

    wam = C.load_wam(scaling_w=args.scaling_w)
    comp = round(args.comp if args.comp >= 0
                 else min(0.8, max(0.3, 0.4 + 0.2 * (args.scaling_w - 1.5))), 2)
    print(f"[embed] 强度 scaling_w={args.scaling_w}  色彩回补 comp={comp}"
          f"{'(自动)' if args.comp < 0 else ''}", flush=True)
    msg1 = torch.from_numpy(C.wm_msg_bits()).float().unsqueeze(0).cuda()

    dec = subprocess.Popen(
        [C.FF, "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-vf", "scale=in_color_matrix=bt709", "-"],
        stdout=subprocess.PIPE)
    enc = subprocess.Popen(
        [C.FF, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "-",
         "-i", str(src),
         "-map", "0:v", "-map", "1:a?",
         "-vf", "scale=out_color_matrix=bt709,format=yuv420p,"
                "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf),
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-c:a", "copy", str(out_path)],
        stdin=subprocess.PIPE)

    frame_bytes = w * h * 3
    psnr_samples = []
    n_done = 0
    t0 = time.perf_counter()
    t_embed = 0.0
    peak_db = None
    try:
        while True:
            raw = read_exact(dec.stdout, frame_bytes * B)
            if raw is None:
                break
            batch = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)
            tg = time.perf_counter()
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
            p = (-10 * torch.log10(mse + 1e-12)).cpu().numpy()
            psnr_samples.append(p)
            wm = y8.permute(0, 2, 3, 1).cpu().numpy()
            t_embed += time.perf_counter() - tg
            enc.stdin.write(wm.tobytes())
            n_done += len(batch)
            if n_done % 1600 < B:
                el = time.perf_counter() - t0
                eta = f", 预计还需 {el / n_done * (total_frames - n_done):.0f}s" if total_frames else ""
                print(f"  进度 {n_done}/{total_frames or '?'} 帧 ({el:.0f}s{eta})", flush=True)
            del x, base, out, y8, yf, mse, wm
            if len(batch) < B:
                break
    finally:
        try:
            enc.stdin.close()
        except Exception:  # 原异常优先:管道已断时 close 抛 BrokenPipeError 会掩盖真正错误
            pass
        try:
            dec.stdout.close()
        except Exception:
            pass
        rc_dec = dec.wait()
        rc_enc = enc.wait()

    wall = time.perf_counter() - t0
    psnrs = np.concatenate(psnr_samples)
    print(f"[embed] 完成 {n_done} 帧, 总耗时 {wall:.0f}s (嵌入纯GPU {t_embed:.0f}s, "
          f"{1000 * t_embed / max(n_done, 1):.1f} ms/帧), rc_dec={rc_dec} rc_enc={rc_enc}", flush=True)
    print(f"[embed] PSNR mean={psnrs.mean():.2f} min={psnrs.min():.2f} p5={np.percentile(psnrs, 5):.2f}", flush=True)

    # 校验
    probe = subprocess.run(
        [C.FFPROBE, "-v", "error", "-count_frames",
         "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames,width,height,avg_frame_rate,codec_name",
         "-show_entries", "format=duration,size", "-of", "json", str(out_path)],
        capture_output=True, text=True).stdout
    info = json.loads(probe)
    st = info["streams"][0]
    size_mb = int(info["format"]["size"]) / 2**20
    ok = int(st["nb_read_frames"]) == n_done
    print(f"[verify] 帧数={st['nb_read_frames']} (期望 {n_done}) {'✓' if ok else '✗'} "
          f"{st['codec_name']} {st['width']}x{st['height']} @{st['avg_frame_rate']} {size_mb:.0f} MB", flush=True)
    if not ok:
        # 非零退出码:run_all 的 assert rc==0 依赖它拦截坏成品(旧实现只打印 ✗ 会放行)
        raise SystemExit(f"[verify] 帧数校验失败: 成品 {st['nb_read_frames']} 帧 != 嵌入 {n_done} 帧")
    meta = dict(source=str(src), out=str(out_path), frames=int(st["nb_read_frames"]), crf=args.crf,
                wall_s=wall, embed_ms=1000 * t_embed / max(n_done, 1),
                psnr_mean=float(psnrs.mean()), psnr_min=float(psnrs.min()),
                psnr_p5=float(np.percentile(psnrs, 5)), size_mb=size_mb,
                scaling_w=args.scaling_w, comp=comp,
                id_hex=f"{C.derive_id(C.WM_TEXT):08x}")
    (C.OUTPUT / "成品_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[embed] 全部完成", flush=True)


if __name__ == "__main__":
    main()
