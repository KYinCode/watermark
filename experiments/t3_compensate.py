# -*- coding: utf-8 -*-
"""任务3: 成品色偏缓解小样矩阵(交接文档 §3 任务3; 2026-09-06 用户复查后获批开跑)。

用户复查: 白底泛黄、黑元素边缘泛黄(偶发绿)仍明显; 泛紫可接受。
机制(t2b 基线, 成品口径 A_prod_vs_orig):
  白区泛黄   = 蓝通道相对红绿多降 0.64/0.94 级(两段) -> 全局色度差, 可预补偿
  黑边黄绿边 = 4:2:0 色度子采样 + 蚊式噪声的局部效应 -> 全局偏置治不了, 看 crf/强度
矩阵(全部 libx264 -preset medium, yuv420p, bt709 三标签, 与 embed_video.py 同口径; 段=660帧×2):
  comp0.0/comp0.5/comp1.0  sw=2.0 crf14   嵌入前 R,G 各减 c 级(等效蓝差回补)。
                                          不直接加 B: 白底 B=255 无上调空间, 会被削顶。
                                          档位按基线差值 0.6~0.9 定, 1.5 会过冲(故未采用文档初版 1.5)。
  sw15                     sw=1.5 crf14   隐形优先档(水印残差是白区泛黄主导项之一)
  crf12/crf10              sw=2.0         候选A: 高码率(压缩噪声小, 黑边镶边应减轻)
诊断帧 = 5100 / 17900, 五区口径: white/blue/dark/edgeW(白底贴黑边)/edgeD(黑体贴白底), 掩码用未补偿原始帧。
分解: B_comp_only(补偿本身) / C_wm_only(水印残差) / D_enc_only(编码) / A_total(成品vs原始)。
画质: PSNR 残差口径(对补偿后) + 对源口径(含补偿代价); 鲁棒性: 直解 + crf23 重编码(t2b 同口径)。
结果: experiments/out/t3_compensate/summary.json (逐段增量写盘, 断点续跑); 样片 mp4 保留供肉眼比对。
"""
import gc
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import common as C

OUTD = C.OUT / "t3_compensate"
OUTD.mkdir(parents=True, exist_ok=True)
SUMMARY = OUTD / "summary.json"
N = 660
DIAG_LOCAL = 100  # 段内诊断帧偏移: 5000+100=5100, 17800+100=17900
SEGS = [(5000, "A_深蓝网格"), (17800, "B_白底UI")]
ARMS = [
    dict(tag="comp0.0", comp=0.0, sw=2.0, crf=14),
    dict(tag="comp0.5", comp=0.5, sw=2.0, crf=14),
    dict(tag="comp1.0", comp=1.0, sw=2.0, crf=14),
    dict(tag="sw15", comp=0.0, sw=1.5, crf=14),
    dict(tag="sw15c0.5", comp=0.5, sw=1.5, crf=14),
    dict(tag="sw15c0.4", comp=0.4, sw=1.5, crf=14),
    dict(tag="sw15c0.3", comp=0.3, sw=1.5, crf=14),
    dict(tag="crf12", comp=0.0, sw=2.0, crf=12),
    dict(tag="crf10", comp=0.0, sw=2.0, crf=10),
]


def dilate(m, r):
    d = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy or dx:
                d |= np.roll(np.roll(m, dy, 0), dx, 1)
    return d


def region_stats(orig, prod):
    """五区 dR/dG/dB(prod - orig 的区域均值;掩码定义在原始帧上)"""
    o = orig.astype(np.float32)
    q = prod.astype(np.float32)
    d = q - o
    mx = o.max(axis=2)
    mn = o.min(axis=2)
    masks = {
        "white": mn > 200,
        "blue": (o[:, :, 2] > 60) & (o[:, :, 0] < 90) & (o[:, :, 1] < 90),
        "dark": mx < 80,
    }
    masks["edgeW"] = masks["white"] & dilate(masks["dark"], 3)  # 白底贴黑边(用户看黄/绿的地方)
    masks["edgeD"] = masks["dark"] & dilate(masks["white"], 3)
    rows = {}
    for name, m in masks.items():
        if m.sum() < 100:
            rows[name] = None
            continue
        rows[name] = dict(cover=round(float(m.mean()), 3),
                          dR=round(float(d[:, :, 0][m].mean()), 2),
                          dG=round(float(d[:, :, 1][m].mean()), 2),
                          dB=round(float(d[:, :, 2][m].mean()), 2))
    return rows


def cast_metrics(rows):
    out = {}
    w, b = rows.get("white"), rows.get("blue")
    if w:
        out["white_diff_B_vs_RG"] = round(w["dB"] - (w["dR"] + w["dG"]) / 2, 2)
    if b:
        out["blue_extraB"] = round((b["dR"] + b["dG"]) / 2 - b["dB"], 2)
    return out


def start_encoder(path, crf):
    return subprocess.Popen(
        [C.FF, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{C.W}x{C.H}", "-r", str(C.FPS), "-i", "-",
         "-vf", "scale=out_color_matrix=bt709,format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         str(path)],
        stdin=subprocess.PIPE)


def norm_float(bf):
    """BHWC float 0..1 -> BCHW cuda 归一化(补偿发生在 float 域, 可精确 0.5 级)"""
    import torch
    x = torch.from_numpy(bf).cuda().permute(0, 3, 1, 2)
    mean = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)
    return (x - mean) / std


def embed_and_encode(wam, msg1, frames, diag_local, mean_t, std_t, writer, comp):
    """补偿(R,G各减c级) -> 嵌入 -> writer;返回 (残差PSNR, 对源PSNR, 诊断帧组)"""
    import torch
    B = 8
    ps_wm, ps_src = [], []
    orig_diag = comp_diag = pre_diag = None
    gi = 0
    for i in range(0, len(frames), B):
        batch = np.stack(frames[i:i + B])
        bf = batch.astype(np.float32) / 255.0
        if comp:
            bf[:, :, :, 0] = np.clip(bf[:, :, :, 0] - comp / 255.0, 0, 1)
            bf[:, :, :, 1] = np.clip(bf[:, :, :, 1] - comp / 255.0, 0, 1)
        x = norm_float(bf)
        orig = x * std_t + mean_t
        with torch.no_grad():
            out = wam.embed(x, msg1.repeat(x.shape[0], 1))
        y8 = (out["imgs_w"] * std_t + mean_t).clamp(0, 1).mul(255).round().byte()
        yf = y8.float() / 255.0
        src_t = torch.from_numpy(batch).cuda().permute(0, 3, 1, 2).float() / 255.0
        for tgt, acc in ((orig, ps_wm), (src_t, ps_src)):
            mse = ((yf - tgt) ** 2).mean(dim=(1, 2, 3))
            acc.append((-10 * torch.log10(mse + 1e-12)).cpu().numpy())
        wm = y8.permute(0, 2, 3, 1).cpu().numpy()
        writer(wm)
        if gi <= diag_local < gi + len(batch):
            k = diag_local - gi
            orig_diag = batch[k].copy()
            comp_diag = bf[k] * 255.0
            pre_diag = wm[k].copy()
        gi += len(batch)
        del x, orig, out, y8, yf, src_t, wm
    def stat(a):
        v = np.concatenate(a)
        return dict(mean=round(float(v.mean()), 2), min=round(float(v.min()), 2),
                    p5=round(float(np.percentile(v, 5)), 2))
    return stat(ps_wm), stat(ps_src), orig_diag, comp_diag, pre_diag


def measure_robustness(wam, msg_np, sample_path, n=N):
    """直解 + crf23 录屏重编码后解码(t2b/verify 02 项口径)"""
    res = {}
    accs, covs, dms = C.decode_batch_stats(wam, C.read_frames_iter(sample_path, C.W, C.H), n, msg_np)
    res["direct"] = dict(exact=round(float((accs == 1).mean()), 4),
                         ge31=round(float((accs >= 31 / 32).mean()), 4),
                         acc=round(float(accs.mean()), 4), ms=round(dms, 1))
    t23 = OUTD / "tmp_rec23.mp4"
    C.encode_frames(C.read_frames_iter(sample_path, C.W, C.H), t23, 23)
    accs2, _, _ = C.decode_batch_stats(wam, C.read_frames_iter(t23, C.W, C.H), n, msg_np)
    res["rec23"] = dict(exact=round(float((accs2 == 1).mean()), 4),
                        ge31=round(float((accs2 >= 31 / 32).mean()), 4),
                        acc=round(float(accs2.mean()), 4))
    t23.unlink()
    return res


def get_row(summary, arm):
    for r in summary["rows"]:
        if r["tag"] == arm["tag"]:
            return r
    r = dict(kind="t3", tag=arm["tag"], comp=arm["comp"], sw=arm["sw"], crf=arm["crf"], segs={})
    summary["rows"].append(r)
    return r


def main():
    import torch
    src = C.one_video()
    msg_np = C.wm_msg_bits()
    msg1 = torch.from_numpy(msg_np).float().unsqueeze(0).cuda()
    mean_t = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std_t = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)

    meta = dict(src=src.name, n=N, segs=[[s, t] for s, t in SEGS],
                arms=[{k: a[k] for k in ("tag", "comp", "sw", "crf")} for a in ARMS],
                encoder="libx264 preset medium yuv420p bt709 三标签 (embed_video.py 同口径)",
                comp_note="comp=R,G 嵌入前各减 c 级(float 域); 白区差值基线 -0.64/-0.94 级",
                diag_frames=[5000 + DIAG_LOCAL, 17800 + DIAG_LOCAL])
    if SUMMARY.exists():
        try:
            summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
            summary["meta"] = meta
        except Exception:
            summary = {"meta": meta, "rows": []}
    else:
        summary = {"meta": meta, "rows": []}

    wam = C.load_wam(scaling_w=2.0)
    print("[t3] 模型就绪", flush=True)

    for arm in ARMS:
        row = get_row(summary, arm)
        for start, tagname in SEGS:
            if tagname in row["segs"]:
                print(f"[t3][{arm['tag']}][{tagname}] 已有结果,跳过", flush=True)
                continue
            wam.scaling_w = arm["sw"]
            torch.cuda.empty_cache()
            t0 = time.perf_counter()
            frames = C.read_frames(src, start, N)
            assert len(frames) == N, f"只读到 {len(frames)} 帧"
            sample = OUTD / f"seg{start}_{arm['tag']}.mp4"
            enc = start_encoder(sample, arm["crf"])
            ps_wm, ps_src, od, cd, pd = embed_and_encode(
                wam, msg1, frames, DIAG_LOCAL, mean_t, std_t,
                lambda wm: enc.stdin.write(wm.tobytes()), arm["comp"])
            enc.stdin.close()
            rc = enc.wait()
            assert rc == 0, f"x264 编码失败 rc={rc}"
            enc_s = time.perf_counter() - t0

            prod_diag = C.read_frames(sample, DIAG_LOCAL, 1)[0]
            total = region_stats(od, prod_diag)
            segdata = dict(sample=sample.name, enc_s=round(enc_s, 1),
                           psnr_wm=ps_wm, psnr_src=ps_src,
                           A_total=total, cast_total=cast_metrics(total),
                           B_comp_only=cast_metrics(region_stats(od, cd)),
                           C_wm_only=cast_metrics(region_stats(cd, pd)),
                           D_enc_only=cast_metrics(region_stats(pd, prod_diag)),
                           zones_enc_only=region_stats(pd, prod_diag))
            print(f"[t3][{arm['tag']}][{tagname}] 嵌入+编码 {enc_s:.0f}s  "
                  f"PSNR残差={ps_wm['mean']} 对源={ps_src['mean']}  "
                  f"白差={segdata['cast_total'].get('white_diff_B_vs_RG')}  "
                  f"蓝extra={segdata['cast_total'].get('blue_extraB')}", flush=True)

            rob = measure_robustness(wam, msg_np, sample)
            segdata["robustness"] = rob
            print(f"[t3][{arm['tag']}][{tagname}] 直解 exact={rob['direct']['exact']:.1%}  "
                  f"crf23 exact={rob['rec23']['exact']:.1%}  "
                  f"段完成 {time.perf_counter()-t0:.0f}s", flush=True)
            row["segs"][tagname] = segdata
            SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            del frames, od, cd, pd, prod_diag
            gc.collect()
            torch.cuda.empty_cache()

        print(f"[t3] === {arm['tag']} 完成,summary 已写 ===", flush=True)

    print("[t3] 矩阵完成 ->", SUMMARY, flush=True)


if __name__ == "__main__":
    main()
