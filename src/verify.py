# -*- coding: utf-8 -*-
"""成品验收: 对最终成品视频重测需求书全部量化项(两段各 660 帧)。
用法: python src/verify.py --product DeepSeek+DSH_已加水印_v2.mp4 --start 5000 [--start 17800]
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import common as C
import s2_attacks as S
import wmlog

log = logging.getLogger("wm.cli.verify")

TMP = C.OUT / "verify_tmp"
TMP.mkdir(parents=True, exist_ok=True)
N = 660


def inv_rot_crop(f, angle):
    h, w = f.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    r = cv2.warpAffine(f, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    ch, cw = min(C.H, h), min(C.W, w)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return r[y0:y0 + ch, x0:x0 + cw]


def angle_best(wam, src, angles, msg_np, sub=60):
    frames = []
    for i, f in enumerate(C.read_frames_iter_auto(src)):
        if i >= sub:
            break
        frames.append(f)
    votes = {}
    for a in angles:
        accs, _, _ = C.decode_batch_stats(wam, (inv_rot_crop(f, a) for f in frames), len(frames), msg_np, report_ms=False)
        votes[a] = int((accs == 1).sum())
    return max(votes, key=votes.get)


def report(wam, name, it, n, msg_np, results):
    accs, covs, dms = C.decode_batch_stats(wam, it, n, msg_np)
    r = dict(name=name, n=n, exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()),
             acc=float(accs.mean()), cov=float(covs.mean()))
    results.append(r)
    print(f"  {name:<30} exact={r['exact']:6.1%} ge31={r['ge31']:6.1%} acc={r['acc']:.4f} cov={r['cov']:.3f}", flush=True)
    log.info("攻击项 %s exact=%.1f%% ge31=%.1f%% acc=%.4f cov=%.3f",
             name, r["exact"] * 100, r["ge31"] * 100, r["acc"], r["cov"])
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default=None, help="默认取 output\ 下最新的 *_已加水印.mp4")
    ap.add_argument("--start", type=int, nargs="+", default=[5000])
    ap.add_argument("--log-file", type=str, default=None,
                    help="日志文件(可选):验收流水带时间戳写入该文件,控制台输出不变")
    args = ap.parse_args()
    if args.log_file:
        wmlog.setup_cli(args.log_file)
    product = Path(args.product) if args.product else max(
        C.OUTPUT.glob("*_已加水印*.mp4"), key=lambda q: q.stat().st_mtime)
    msg_np = C.wm_msg_bits()
    wam = C.load_wam(scaling_w=2.0)  # 只加载一次,多段共用(旧实现每段各重载,浪费一次 ~10s)
    for start in args.start:
        run_segment(product, start, msg_np, wam)


def run_segment(product, args_start, msg_np, wam):
    seg_tag = f"seg{args_start}"
    N_ = N
    results = []

    # 抽取成品段为中间 mp4(与源段对齐, 帧精确)
    prod_seg = TMP / f"prod_{seg_tag}.mp4"
    part = TMP / f"prod_{seg_tag}.mp4.part"
    if not prod_seg.exists():
        # 先写 .part 再改名:中断残留不会被当作有效缓存(旧实现"存在即复用"会在坏数据上测所有攻击项)
        C.encode_frames(C.read_frames(product, args_start, N), part, 14)  # 近无损中转,只用于稳定读写
        part.replace(prod_seg)
    print(f"[verify] {product.name} @ 帧{args_start}-{args_start + N - 1}", flush=True)
    log.info("开始验收 %s @ 帧%d-%d", product.name, args_start, args_start + N - 1)

    # 1) 1:1 基线(成品直解,精确 660 帧列表,不经中转)
    report(wam, "01_1to1直解", C.read_frames(product, args_start, N), N, msg_np, results)
    # 2) 全屏录屏 crf23
    t = TMP / f"v_{seg_tag}_rec23.mp4"
    C.encode_frames(C.read_frames_iter(prod_seg, C.W, C.H), t, 23)
    report(wam, "02_录屏crf23", C.read_frames_iter(t, C.W, C.H), N, msg_np, results)
    # 3) 二压 crf18 / crf23
    t = TMP / f"v_{seg_tag}_rec18.mp4"
    C.encode_frames(C.read_frames_iter(prod_seg, C.W, C.H), t, 18)
    report(wam, "03_二压crf18", C.read_frames_iter(t, C.W, C.H), N, msg_np, results)
    # 4) 连续 2 代转码
    t1 = TMP / f"v_{seg_tag}_g2a.mp4"
    C.encode_frames(C.read_frames_iter(prod_seg, C.W, C.H), t1, 23)
    t2 = TMP / f"v_{seg_tag}_g2b.mp4"
    C.encode_frames(C.read_frames_iter(t1, C.W, C.H), t2, 23)
    report(wam, "04_连续2代crf23", C.read_frames_iter(t2, C.W, C.H), N, msg_np, results)
    # 5) 帧率 60->30
    t = TMP / f"v_{seg_tag}_fps30.mp4"
    C.encode_frames(C.read_frames_iter(prod_seg, C.W, C.H), t, 23, fps=30)
    report(wam, "05_60to30fps", C.read_frames_iter(t, C.W, C.H), N // 2, msg_np, results)
    # 6) 微信级 720p crf28
    t = TMP / f"v_{seg_tag}_wx720.mp4"
    C.encode_frames((cv2.resize(f, (1280, 720), interpolation=cv2.INTER_AREA)
                     for f in C.read_frames_iter(prod_seg, C.W, C.H)), t, 28, w=1280, h=720)
    report(wam, "06_微信720p_crf28", C.read_frames_iter(t, 1280, 720), N, msg_np, results)
    # 7) 降清 720p 直解 / 还原 / 540p
    t = TMP / f"v_{seg_tag}_dn720.mp4"
    C.encode_frames((cv2.resize(f, (1280, 720), interpolation=cv2.INTER_AREA)
                     for f in C.read_frames_iter(prod_seg, C.W, C.H)), t, 23, w=1280, h=720)
    report(wam, "07a_720p直解", C.read_frames_iter(t, 1280, 720), N, msg_np, results)
    report(wam, "07b_720p还原1080", (cv2.resize(f, (C.W, C.H), interpolation=cv2.INTER_LINEAR)
                                     for f in C.read_frames_iter(t, 1280, 720)), N, msg_np, results)
    t = TMP / f"v_{seg_tag}_dn540.mp4"
    C.encode_frames((cv2.resize(f, (960, 540), interpolation=cv2.INTER_AREA)
                     for f in C.read_frames_iter(prod_seg, C.W, C.H)), t, 23, w=960, h=540)
    report(wam, "07c_540p直解", C.read_frames_iter(t, 960, 540), N, msg_np, results)
    # 8) 镜像
    report(wam, "08_水平镜像", (S.mirror(f) for f in C.read_frames_iter(prod_seg, C.W, C.H)), N, msg_np, results)
    # 9) 旋转 5/15/60 + 两步式
    rot_ranges = {5: list(range(-8, 9)), 15: list(range(-18, 19, 2)),
                  60: list(range(48, 73, 2)) + list(range(-72, -47, 2))}
    for ang in (5, 15, 60):
        t = TMP / f"v_{seg_tag}_rot{ang}.mp4"
        probe_frame = S.rotate(np.zeros((C.H, C.W, 3), np.uint8), ang)
        rh, rw = probe_frame.shape[:2]
        C.encode_frames((S.rotate(f, ang) for f in C.read_frames_iter(prod_seg, C.W, C.H)), t, 18, w=rw, h=rh)
        report(wam, f"09_rot{ang}_单步", C.read_frames_iter_auto(t), N, msg_np, results)
        best = angle_best(wam, t, rot_ranges[ang], msg_np)
        accs, _, _ = C.decode_batch_stats(wam, (inv_rot_crop(f, best) for f in C.read_frames_iter_auto(t)),
                                          1, msg_np, report_ms=False)
        results.append(dict(name=f"09_rot{ang}_两步式", inv=int(best), n=N,
                            exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()),
                            acc=float(accs.mean()), cov=-1))
        print(f"  09_rot{ang}_两步式(inv={best})  exact={results[-1]['exact']:6.1%}", flush=True)
    # 10) 裁剪 25/36/50%
    rng = np.random.default_rng(42)
    for area, tagname in ((0.25, "25"), (0.36, "36"), (0.50, "50")):
        cw, ch = int(C.W * area ** 0.5) // 2 * 2, int(C.H * area ** 0.5) // 2 * 2
        accs_all = []
        for inst in range(3):
            x0 = int(rng.integers(0, C.W - cw + 1)) // 2 * 2
            y0 = int(rng.integers(0, C.H - ch + 1)) // 2 * 2
            lo, hi = inst * N // 3, (inst + 1) * N // 3
            it = (f[y0:y0 + ch, x0:x0 + cw] for i, f in enumerate(C.read_frames_iter(prod_seg, C.W, C.H)) if lo <= i < hi)
            accs, _, _ = C.decode_batch_stats(wam, it, hi - lo, msg_np, report_ms=False)
            accs_all.append(accs)
        accs_all = np.concatenate(accs_all)
        results.append(dict(name=f"10_裁剪{tagname}%", n=len(accs_all), exact=float((accs_all == 1).mean()),
                            ge31=float((accs_all >= 31 / 32).mean()), acc=float(accs_all.mean()), cov=-1))
        print(f"  10_裁剪{tagname}%            exact={results[-1]['exact']:6.1%}", flush=True)
    # 11) 包边 70/90 + 两步式
    for scale in (0.70, 0.90):
        t = TMP / f"v_{seg_tag}_canvas{int(scale*100)}.mp4"
        C.encode_frames((S.canvas_paste(f, scale) for f in C.read_frames_iter(prod_seg, C.W, C.H)), t, 18)
        report(wam, f"11_canvas{int(scale*100)}_单步", C.read_frames_iter_auto(t), N, msg_np, results)
        # 两步式: 中心 50% 窗(纯内容区)
        report(wam, f"11_canvas{int(scale*100)}_两步窗", (f[270:810, 480:1440] for f in C.read_frames_iter_auto(t)),
               N, msg_np, results)
    # 12) 画中画 25% 被覆盖
    src_vid = C.one_video()
    other = C.read_frames(src_vid, 12000, 60) or C.read_frames(src_vid, 0, 60)
    def pip_gen():
        for i, f in enumerate(C.read_frames_iter(prod_seg, C.W, C.H)):
            yield S.pip_paste(f, other[i % len(other)])
    t = TMP / f"v_{seg_tag}_pip.mp4"
    C.encode_frames(pip_gen(), t, 18)
    report(wam, "12_画中画25%", C.read_frames_iter_auto(t), N, msg_np, results)
    # 13) 画质攻击
    ops = [("亮+20%", lambda f: S.brightness(f, 51)), ("亮-20%", lambda f: S.brightness(f, -51)),
           ("对比+20%", lambda f: S.contrast(f, 1.2)), ("对比-20%", lambda f: S.contrast(f, 0.8)),
           ("饱和+20%", lambda f: S.saturation(f, 1.2)), ("饱和-20%", lambda f: S.saturation(f, 0.8)),
           ("色相+20%", lambda f: S.hue_shift(f, 36)), ("色相-20%", lambda f: S.hue_shift(f, -36)),
           ("高斯模糊", lambda f: S.blur(f)), ("椒盐5%", lambda f: S.saltpepper(f))]
    for name, fn in ops:
        report(wam, f"13_{name}", (fn(f) for f in C.read_frames_iter(prod_seg, C.W, C.H)), N, msg_np, results)
    # 13b) 工具链(逆补偿网格): 攻击后逐档逆补偿,每帧取网格最优;对应 extract_wm 策略链的补偿段
    def toolchain(name, attack, comps):
        accs_all = []
        for comp in comps:
            accs, _, _ = C.decode_batch_stats(
                wam, (comp(attack(f)) for f in C.read_frames_iter(prod_seg, C.W, C.H)),
                N, msg_np, report_ms=False)
            accs_all.append(accs)
        best = np.stack(accs_all).max(axis=0)
        results.append(dict(name=name, n=N, exact=float((best == 1).mean()),
                            ge31=float((best >= 31 / 32).mean()), acc=float(best.mean()), cov=-1))
        print(f"  {name:<30} exact={results[-1]['exact']:6.1%} ge31={results[-1]['ge31']:6.1%}", flush=True)

    toolchain("13_亮-20%_工具链", lambda f: S.brightness(f, -51),
              [lambda f, d=d: S.brightness(f, d) for d in (13, 26, 39, 51)])
    toolchain("13_色相-20%_工具链", lambda f: S.hue_shift(f, -36),
              [lambda f, s=s: S.hue_shift(f, s) for s in (12, 24, 36, 48, 60, 72)])
    # 14) 遮挡 10%
    accs_all = []
    for inst in range(3):
        x0 = int(rng.integers(0, C.W - 576 + 1)) // 2 * 2
        y0 = int(rng.integers(0, C.H - 360 + 1)) // 2 * 2
        it = (S.occlude(f, (x0, y0, 576, 360), rng) for f in C.read_frames_iter(prod_seg, C.W, C.H))
        accs, _, _ = C.decode_batch_stats(wam, it, N, msg_np, report_ms=False)
        accs_all.append(accs)
    accs_all = np.concatenate(accs_all)
    results.append(dict(name="14_随机遮挡10%", n=len(accs_all), exact=float((accs_all == 1).mean()),
                        ge31=float((accs_all >= 31 / 32).mean()), acc=float(accs_all.mean()), cov=-1))
    print(f"  14_随机遮挡10%          exact={results[-1]['exact']:6.1%}", flush=True)
    # 15) 组合搬运 + 镜像重试
    def combo(f):
        g = S.mirror(f)
        x0, y0 = int(C.W * 0.1), int(C.H * 0.1)
        g = g[y0:y0 + int(C.H * 0.8), x0:x0 + int(C.W * 0.8)]
        g = S.contrast(g, 1.2); g = S.saturation(g, 1.2); g = S.hue_shift(g, 18)
        g = cv2.resize(g, (1280, 720), interpolation=cv2.INTER_AREA)
        g[720 - 100:720 - 10, 1280 - 270:1280 - 10] = 255
        g[720 - 70:720 - 40, 1280 - 250:1280 - 60] = 40
        return g
    t = TMP / f"v_{seg_tag}_combo.mp4"
    C.encode_frames((combo(f) for f in C.read_frames_iter(prod_seg, C.W, C.H)), t, 23, w=1280, h=720)
    report(wam, "15a_组合搬运_单步", C.read_frames_iter_auto(t), N, msg_np, results)
    report(wam, "15b_组合搬运_镜像重试", (np.ascontiguousarray(f[:, ::-1]) for f in C.read_frames_iter_auto(t)),
           N, msg_np, results)

    out = C.VERIFY_DIR / f"verify_{seg_tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(product=product.name, start=args_start, n=N, results=results),
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[verify] 完成 -> {out}", flush=True)
    log.info("验收完成 %d 项 -> %s", len(results), out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log.exception("运行失败")
        raise SystemExit(1)
