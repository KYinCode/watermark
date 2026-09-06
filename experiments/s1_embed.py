# -*- coding: utf-8 -*-
"""s1: 从源视频取 >=600 帧连续段,逐帧 WAM 嵌入,测 PSNR/耗时,编码成品档 crf14。
用法: python s1_embed.py --start 5000 --n 660 --scaling_w 2.0 --tag sw20
"""
import argparse
import json
import time

import numpy as np

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=5000)
    ap.add_argument("--n", type=int, default=660)
    ap.add_argument("--scaling_w", type=float, default=2.0)
    ap.add_argument("--tag", type=str, required=True)
    ap.add_argument("--crf", type=int, default=14)
    args = ap.parse_args()

    outdir = C.OUT / f"seg_{args.tag}"
    outdir.mkdir(parents=True, exist_ok=True)
    C.write_codebook(C.PROJ / "codebook.json")

    print(f"[s1] 读源段 帧{args.start}-{args.start + args.n - 1} ...", flush=True)
    t0 = time.perf_counter()
    frames = C.read_frames(C.one_video(), args.start, args.n)
    assert len(frames) == args.n, f"只读到 {len(frames)} 帧"
    print(f"[s1] 解码 {len(frames)} 帧 {time.perf_counter()-t0:.1f}s", flush=True)

    wam = C.load_wam(scaling_w=args.scaling_w)
    import torch
    msg1 = torch.from_numpy(C.wm_msg_bits()).float().unsqueeze(0).cuda()  # 1x32

    wm_frames = []
    psnrs = []
    B = 8  # 显存安全档:实测峰值 ~2.5GiB,避免 WDDM 倒腾共享内存(3D 打满/CUDA 饿死)
    mean_t = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std_t = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)
    torch.cuda.synchronize()
    t_embed = time.perf_counter()
    with torch.no_grad():
        for i in range(0, len(frames), B):
            x = C.norm_frames(np.stack(frames[i:i + B]))
            orig = x * std_t + mean_t                       # 0..1 原图
            out = wam.embed(x, msg1.repeat(x.shape[0], 1))
            y8 = (out["imgs_w"] * std_t + mean_t).clamp(0, 1).mul(255).round().byte()
            yf = y8.float() / 255.0
            mse = ((yf - orig) ** 2).mean(dim=(1, 2, 3))
            # mse 为 [0,1] 空间,PSNR_8bit = -10*log10(mse_01)
            psnrs.append((-10 * torch.log10(mse + 1e-12)).cpu())
            wm_frames.append(y8.permute(0, 2, 3, 1).cpu().numpy())
            del x, orig, out, y8, yf, mse
    wm_frames = np.concatenate(wm_frames)
    psnrs = torch.cat(psnrs).numpy()
    torch.cuda.synchronize()
    emb_s = time.perf_counter() - t_embed
    peak = torch.cuda.max_memory_allocated() / 2**30
    psnrs = np.array(psnrs)
    print(f"[s1] 嵌入 {len(frames)} 帧 {emb_s:.1f}s ({1000*emb_s/len(frames):.1f} ms/帧), 峰值显存 {peak:.2f} GiB", flush=True)
    print(f"[s1] PSNR mean={psnrs.mean():.2f} median={np.median(psnrs):.2f} min={psnrs.min():.2f} p5={np.percentile(psnrs,5):.2f}", flush=True)

    t0 = time.perf_counter()
    out_mp4 = outdir / "wm.mp4"
    C.encode_frames(wm_frames, out_mp4, args.crf)
    enc_s = time.perf_counter() - t0
    size_mb = out_mp4.stat().st_size / 2**20
    print(f"[s1] 编码 crf{args.crf} {enc_s:.1f}s ({1000*enc_s/len(frames):.1f} ms/帧), {size_mb:.1f} MB", flush=True)

    # 抽样存对比图(供肉眼检查): 原图/加水印/差异放大10x
    import cv2
    for idx in (0, len(frames) // 2, len(frames) - 1):
        diff = np.abs(frames[idx].astype(np.int16) - wm_frames[idx].astype(np.int16))
        diff = np.clip(diff * 10, 0, 255).astype(np.uint8)
        C.imwrite_unicode(outdir / f"sample_{idx}_orig.png", cv2.cvtColor(frames[idx], cv2.COLOR_RGB2BGR))
        C.imwrite_unicode(outdir / f"sample_{idx}_wm.png", cv2.cvtColor(wm_frames[idx], cv2.COLOR_RGB2BGR))
        C.imwrite_unicode(outdir / f"sample_{idx}_diffx10.png", cv2.cvtColor(diff, cv2.COLOR_RGB2BGR))

    # 快速自检: 嵌入后直接解码 30 帧,确认信息注入成功
    accs, covs, dms = C.decode_batch_stats(wam, wm_frames[:30], 30, C.wm_msg_bits())
    print(f"[s1] 自检直解 30 帧: bit_acc mean={accs.mean():.4f} exact={(accs==1).mean():.1%} cov mean={covs.mean():.3f} ({dms:.0f} ms/帧)", flush=True)

    meta = dict(tag=args.tag, start=args.start, n=len(frames), scaling_w=args.scaling_w, crf=args.crf,
                psnr_mean=float(psnrs.mean()), psnr_median=float(np.median(psnrs)), psnr_min=float(psnrs.min()),
                psnr_p5=float(np.percentile(psnrs, 5)),
                embed_ms=float(1000 * emb_s / len(frames)), encode_ms=float(1000 * enc_s / len(frames)),
                decode_ms=float(dms), peak_mem_gib=float(peak), size_mb=float(size_mb),
                selfcheck_exact=float((accs == 1).mean()), selfcheck_acc=float(accs.mean()))
    (outdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("[s1] 完成", flush=True)


if __name__ == "__main__":
    main()
