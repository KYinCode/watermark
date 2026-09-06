# -*- coding: utf-8 -*-
"""任务2b: 10-bit 编码 × 三强度小样矩阵(交接文档 §2 任务2b)。

每强度两段各 660 帧:
  A 深蓝网格段 帧5000-5659 (诊断帧 5100)
  B 白底UI段   帧17800-18459 (诊断帧 17900)
编码: -c:v libx265 -crf 14 -pix_fmt yuv420p10le -tag:v hvc1 (preset medium)
每段回答:
  色偏   A/B/C 三截面(成品vs原始 / 纯水印 / 纯编码),派生指标: 深蓝区 ΔB 相对红绿多降
  鲁棒性 10bit 成品直解 + crf23 录屏重编码后解码(参考 verify.py 02 项)
  画质   嵌入 PSNR(编码前, 8bit 空间)
另测 8bit 基线(现役成品 DeepSeek+DSH_已加水印_v2.mp4, sw=2.0)同口径诊断,便于对照。
结果: experiments/out/t2b_matrix/summary.json (逐强度增量写入)
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

OUTD = C.OUT / "t2b_matrix"
OUTD.mkdir(parents=True, exist_ok=True)
SUMMARY = OUTD / "summary.json"
N = 660
DIAG_LOCAL = 100  # 段内诊断帧偏移: 5000+100=5100, 17800+100=17900
SEGS = [(5000, "A_深蓝网格"), (17800, "B_白底UI")]
STRENGTHS = [1.5, 2.0, 3.0]
PRODUCT8 = C.OUTPUT / "DeepSeek+DSH_已加水印_v2.mp4"


def region_stats(orig, prod):
    o = orig.astype(np.float32)
    q = prod.astype(np.float32)
    d = q - o
    rows = {}
    for name, m in (("white", o.min(axis=2) > 200),
                    ("blue", (o[:, :, 2] > 60) & (o[:, :, 0] < 90) & (o[:, :, 1] < 90))):
        if m.sum() < 100:
            rows[name] = None
            continue
        rows[name] = dict(cover=round(float(m.mean()), 3),
                          dR=round(float(d[:, :, 0][m].mean()), 2),
                          dG=round(float(d[:, :, 1][m].mean()), 2),
                          dB=round(float(d[:, :, 2][m].mean()), 2))
    return rows


def extra_b_drop(sec):
    """深蓝区 ΔB 相对红绿的多降(正=蓝掉得更多)。两种口径都给。"""
    if not sec or sec.get("blue") is None:
        return None
    b = sec["blue"]
    return dict(vs_meanRG=round((b["dR"] + b["dG"]) / 2 - b["dB"], 2),
                vs_R=round(b["dR"] - b["dB"], 2))


def start_encoder(path):
    return subprocess.Popen(
        [C.FF, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{C.W}x{C.H}", "-r", str(C.FPS), "-i", "-",
         "-vf", "scale=out_color_matrix=bt709,format=yuv420p10le",
         "-c:v", "libx265", "-preset", "medium", "-crf", "14", "-tag:v", "hvc1",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         str(path)],
        stdin=subprocess.PIPE)


def embed_and_encode(wam, msg1, frames, diag_local, mean_t, std_t, writer):
    """逐批嵌入 -> writer(编码器 stdin);返回 (psnr统计, 原始诊断帧, 编码前水印诊断帧)"""
    import torch
    B = 8
    psnrs = []
    orig_diag = pre_diag = None
    gi = 0
    for i in range(0, len(frames), B):
        batch = frames[i:i + B]
        x = C.norm_frames(np.stack(batch))
        orig = x * std_t + mean_t
        with torch.no_grad():
            out = wam.embed(x, msg1.repeat(x.shape[0], 1))
        y8 = (out["imgs_w"] * std_t + mean_t).clamp(0, 1).mul(255).round().byte()
        yf = y8.float() / 255.0
        mse = ((yf - orig) ** 2).mean(dim=(1, 2, 3))
        psnrs.append((-10 * torch.log10(mse + 1e-12)).cpu().numpy())
        wm = y8.permute(0, 2, 3, 1).cpu().numpy()
        writer(wm)
        if gi <= diag_local < gi + len(batch):
            k = diag_local - gi
            orig_diag = batch[k].copy()
            pre_diag = wm[k].copy()
        gi += len(batch)
        del x, orig, out, y8, yf, mse, wm
    p = np.concatenate(psnrs)
    return dict(mean=round(float(p.mean()), 2), min=round(float(p.min()), 2),
                p5=round(float(np.percentile(p, 5)), 2)), orig_diag, pre_diag


def measure_robustness(wam, msg_np, sample_path, n=N):
    """直解 + crf23 录屏重编码后解码(verify.py 02 项口径)"""
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


def diag_product8(wam, msg1, src, src_idx):
    """8bit 基线诊断: 现役成品帧 + 重嵌编码前帧"""
    import torch
    orig = C.read_frames(src, src_idx, 1)[0]
    prod = C.read_frames(PRODUCT8, src_idx, 1)[0]
    with torch.no_grad():
        out = wam.embed(C.norm_frames(orig[None]), msg1)
        pre = C.unnorm_to_uint8(out["imgs_w"])[0]
    A = region_stats(orig, prod)
    Csec = region_stats(pre, prod)
    return dict(A_prod_vs_orig=A, C_enc_only=Csec, extra_B_drop_C=extra_b_drop(Csec),
                extra_B_drop_A=extra_b_drop(A))


def main():
    import torch
    src = C.one_video()
    msg_np = C.wm_msg_bits()
    msg1 = torch.from_numpy(msg_np).float().unsqueeze(0).cuda()
    mean_t = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std_t = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)

    summary = {"meta": dict(src=src.name, n=N, segs=[[s, t] for s, t in SEGS], strengths=STRENGTHS,
                            encoder="libx265 crf14 yuv420p10le hvc1 preset medium",
                            diag_frames=[5100, 17900], product8=PRODUCT8.name), "rows": []}

    wam = C.load_wam(scaling_w=2.0)  # 只加载一次,按档位改 scaling_w
    print("[t2b] 模型就绪", flush=True)

    # ---- 0) 8bit 基线(同口径,便于对照达标线) ----
    base_row = dict(kind="baseline_8bit_h264", sw=2.0, segs={})
    for start, tag in SEGS:
        d = diag_product8(wam, msg1, src, start + DIAG_LOCAL)
        base_row["segs"][tag] = d
        wj = d["A_prod_vs_orig"].get("white")
        print(f"[t2b][baseline8][{tag}] A白ΔB={wj['dB'] if wj else '?'}  "
              f"A蓝={d['A_prod_vs_orig'].get('blue')}  extraB(C)={d['extra_B_drop_C']}", flush=True)
    summary["rows"].append(base_row)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[t2b] 基线写盘", flush=True)

    # ---- 1) 三强度矩阵 ----
    for sw in STRENGTHS:
        wam.scaling_w = sw
        torch.cuda.empty_cache()
        row = dict(kind="x265_10bit", sw=sw, segs={})
        for start, tag in SEGS:
            t0 = time.perf_counter()
            frames = C.read_frames(src, start, N)
            assert len(frames) == N, f"只读到 {len(frames)} 帧"
            sample = OUTD / f"seg{start}_sw{int(sw*10):02d}_10bit.mp4"
            enc = start_encoder(sample)
            box = {}

            def writer(wm):
                enc.stdin.write(wm.tobytes())

            psnr_s, od, pd = embed_and_encode(wam, msg1, frames, DIAG_LOCAL, mean_t, std_t, writer)
            enc.stdin.close()
            rc = enc.wait()
            assert rc == 0, f"x265 编码失败 rc={rc}"
            enc_s = time.perf_counter() - t0

            prod_diag = C.read_frames(sample, DIAG_LOCAL, 1)[0]
            A = region_stats(od, prod_diag)
            Bsec = region_stats(od, pd)
            Csec = region_stats(pd, prod_diag)
            row["segs"][tag] = dict(sample=sample.name, psnr=psnr_s,
                                    A_prod_vs_orig=A, B_prewm_vs_orig=Bsec, C_enc_only=Csec,
                                    extra_B_drop_C=extra_b_drop(Csec), extra_B_drop_A=extra_b_drop(A))
            wj = A.get("white")
            print(f"[t2b][sw{sw}][{tag}] 嵌入+编码 {enc_s:.0f}s  PSNR={psnr_s}  "
                  f"A白ΔB={wj['dB'] if wj else '?'}  extraB(C)={row['segs'][tag]['extra_B_drop_C']}", flush=True)

            rob = measure_robustness(wam, msg_np, sample)
            row["segs"][tag]["robustness"] = rob
            print(f"[t2b][sw{sw}][{tag}] 直解 exact={rob['direct']['exact']:.1%}  "
                  f"crf23 exact={rob['rec23']['exact']:.1%}", flush=True)
            del frames, od, pd, prod_diag
            gc.collect()
            torch.cuda.empty_cache()
            print(f"[t2b][sw{sw}][{tag}] 段完成 {time.perf_counter()-t0:.0f}s", flush=True)

        summary["rows"].append(row)
        SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[t2b] === sw={sw} 全部完成,summary 已写 ===", flush=True)

    print("[t2b] 矩阵完成 ->", SUMMARY, flush=True)


if __name__ == "__main__":
    main()
