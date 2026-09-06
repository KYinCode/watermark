# -*- coding: utf-8 -*-
"""s2: 对成品段逐项攻击 + 逐帧解码统计(全部基于 >=600 帧连续段)。
用法: python s2_attacks.py --tag sw20 [--suite full|core]
输入: exp/out/seg_{tag}/wm.mp4 (crf14 成品档)
"""
import argparse
import json
import time

import cv2
import numpy as np

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C

TMP = C.OUT / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


# ---------- 攻击原语(输入输出均为 uint8 RGB) ----------

def mirror(f):
    return f[:, ::-1].copy()


def rotate(f, angle):
    h, w = f.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    nw -= nw % 2  # x264 要求偶数尺寸
    nh -= nh % 2
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(f, M, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))


def brightness(f, delta):
    return np.clip(f.astype(np.int16) + delta, 0, 255).astype(np.uint8)


def contrast(f, factor):
    return np.clip((f.astype(np.float32) - 128) * factor + 128, 0, 255).astype(np.uint8)


def saturation(f, factor):
    hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1].astype(np.float32) * factor, 0, 255).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)


def hue_shift(f, units):
    """OpenCV hue 单位(180=360°)"""
    hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV).astype(np.int16)
    hsv[:, :, 0] = (hsv[:, :, 0] + units) % 180
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)


def blur(f, k=7, sigma=2.0):
    return cv2.GaussianBlur(f, (k, k), sigma)


def saltpepper(f, p=0.05):
    out = f.copy()
    rnd = np.random.default_rng(7)
    m = rnd.random(f.shape[:2])
    out[m < p / 2] = 0
    out[(m >= p / 2) & (m < p)] = 255
    return out


def occlude(f, rect, rng):
    x0, y0, w, h = rect
    out = f.copy()
    patch = (np.ones((h, w, 3), np.float32) * 110 + rng.normal(0, 12, (h, w, 3))).clip(0, 255).astype(np.uint8)
    out[y0:y0 + h, x0:x0 + w] = patch
    return out


def canvas_paste(f, scale):
    """包边: 原画面等比缩放 scale,置于自身高斯模糊背景画布中央(1920x1080)"""
    bg = cv2.GaussianBlur(f, (0, 0), 12)
    nw, nh = int(C.W * scale), int(C.H * scale)
    content = cv2.resize(f, (nw, nh), interpolation=cv2.INTER_AREA)
    bg[(C.H - nh) // 2:(C.H - nh) // 2 + nh, (C.W - nw) // 2:(C.W - nw) // 2 + nw] = content
    return bg


def pip_paste(f, patch_full):
    """画中画: 中央 960x540 被其他画面覆盖"""
    ph, pw = patch_full.shape[:2]
    y0, x0 = max((ph - 540) // 2, 0), max((pw - 960) // 2, 0)
    patch = patch_full[y0:y0 + 540, x0:x0 + 960]
    out = f.copy()
    out[(C.H - 540) // 2:(C.H - 540) // 2 + 540, (C.W - 960) // 2:(C.W - 960) // 2 + 960] = patch
    return out


def crop_gen(frames_iter, n, x0, y0, w, h):
    for f in frames_iter:
        yield f[y0:y0 + h, x0:x0 + w]


def map_gen(fn, frames_iter):
    for f in frames_iter:
        yield fn(f)


# ---------- 主流程 ----------

def report(wam, name, frames_iter, n, msg, results, noenc=None):
    t0 = time.perf_counter()
    accs, covs, dms = C.decode_batch_stats(wam, frames_iter, n, msg)
    exact = float((accs == 1).mean())
    r = dict(name=name, n=n,
             exact=exact,
             ge31=float((accs >= 31 / 32).mean()),
             acc_mean=float(accs.mean()),
             acc_p5=float(np.percentile(accs, 5)) if n else 0.0,
             cov_mean=float(covs.mean()),
             decode_ms=float(dms),
             wall_s=time.perf_counter() - t0)
    results.append(r)
    print(f"  {name:<26} exact={exact:6.1%}  >=31/32={r['ge31']:6.1%}  "
          f"acc={r['acc_mean']:.4f}  cov={r['cov_mean']:.3f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--suite", choices=["full", "core"], default="full")
    ap.add_argument("--variants", type=str, default=None, help="逗号分隔,覆盖默认变体列表")
    ap.add_argument("--suffix", type=str, default="")
    args = ap.parse_args()

    segdir = C.OUT / f"seg_{args.tag}"
    wm_mp4 = segdir / "wm.mp4"
    meta = json.loads((segdir / "meta.json").read_text(encoding="utf-8"))
    start, n = meta["start"], meta["n"]
    msg = C.wm_msg_bits()
    wam = C.load_wam(scaling_w=meta["scaling_w"])
    results = []
    rng = np.random.default_rng(42)

    print(f"[s2] tag={args.tag} suite={args.suite} 段帧{n}", flush=True)

    # 基线: 成品段直接逐帧解码(不重编码)
    report(wam, "A_1to1_直接解码", C.read_frames_iter(wm_mp4, C.W, C.H), n, msg, results)

    if args.variants:
        variants = args.variants.split(",")
    elif args.suite == "core":
        variants = ["rec_crf23", "mirror", "crop25", "canvas70", "dn720", "combo"]
    else:
        variants = ["rec_crf18", "rec_crf23", "mirror", "rot5", "rot15", "rot60",
                    "crop25", "crop36", "crop50", "canvas70", "canvas90", "pip25",
                    "occl10", "bright10", "bright_10", "bright20", "contrast20", "contrast_20",
                    "sat20", "sat_20", "hue20", "hue_20", "blur", "saltpepper",
                    "dn720", "dn720up", "dn540", "combo"]

    # ---------- 重编码/几何/画质类 ----------
    def gen_variant(v):
        """返回 (frames_iter, w, h, crf 或 None)"""
        if v == "rec_crf18":
            return C.read_frames_iter(wm_mp4, C.W, C.H), C.W, C.H, 18
        if v == "rec_crf23":
            return C.read_frames_iter(wm_mp4, C.W, C.H), C.W, C.H, 23
        if v == "mirror":
            return map_gen(mirror, C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, 18
        if v == "rot5":
            return map_gen(lambda f: rotate(f, 5), C.read_frames_iter(wm_mp4, C.W, C.H)), None, None, 18
        if v == "rot15":
            return map_gen(lambda f: rotate(f, 15), C.read_frames_iter(wm_mp4, C.W, C.H)), None, None, 18
        if v == "rot60":
            return map_gen(lambda f: rotate(f, 60), C.read_frames_iter(wm_mp4, C.W, C.H)), None, None, 18
        if v in ("crop25", "crop36", "crop50"):
            area = {"crop25": 0.25, "crop36": 0.36, "crop50": 0.50}[v]
            return ("crop", area)
        if v == "canvas70":
            return map_gen(lambda f: canvas_paste(f, 0.70), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, 18
        if v == "canvas90":
            return map_gen(lambda f: canvas_paste(f, 0.90), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, 18
        if v == "pip25":
            return ("pip",)
        if v == "occl10":
            return ("occl",)
        if v == "bright10":
            return map_gen(lambda f: brightness(f, 26), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "bright_10":
            return map_gen(lambda f: brightness(f, -26), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "bright20":
            return map_gen(lambda f: brightness(f, 51), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "contrast20":
            return map_gen(lambda f: contrast(f, 1.2), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "contrast_20":
            return map_gen(lambda f: contrast(f, 0.8), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "sat20":
            return map_gen(lambda f: saturation(f, 1.2), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "sat_20":
            return map_gen(lambda f: saturation(f, 0.8), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "hue20":
            return map_gen(lambda f: hue_shift(f, 36), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "hue_20":
            return map_gen(lambda f: hue_shift(f, -36), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "blur":
            return map_gen(lambda f: blur(f), C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "saltpepper":
            return map_gen(saltpepper, C.read_frames_iter(wm_mp4, C.W, C.H)), C.W, C.H, None
        if v == "dn720":
            return map_gen(lambda f: cv2.resize(f, (1280, 720), interpolation=cv2.INTER_AREA),
                           C.read_frames_iter(wm_mp4, C.W, C.H)), 1280, 720, 23
        if v == "dn720up":
            it = map_gen(lambda f: cv2.resize(f, (1280, 720), interpolation=cv2.INTER_AREA),
                         C.read_frames_iter(wm_mp4, C.W, C.H))
            it = map_gen(lambda f: cv2.resize(f, (C.W, C.H), interpolation=cv2.INTER_LINEAR), it)
            return it, C.W, C.H, 23
        if v == "dn540":
            return map_gen(lambda f: cv2.resize(f, (960, 540), interpolation=cv2.INTER_AREA),
                           C.read_frames_iter(wm_mp4, C.W, C.H)), 960, 540, 23
        if v == "gen2":
            # 连续 2 代转码: crf23 -> crf23
            return ("gen2",)
        if v == "fps30":
            it = map_gen(lambda f: cv2.resize(f, (C.W, C.H), interpolation=cv2.INTER_AREA),
                         C.read_frames_iter(wm_mp4, C.W, C.H))
            return ("fps30", it)
        if v == "wx720":
            return map_gen(lambda f: cv2.resize(f, (1280, 720), interpolation=cv2.INTER_AREA),
                           C.read_frames_iter(wm_mp4, C.W, C.H)), 1280, 720, 28
        if v == "combo":
            def combo(f):
                g = mirror(f)
                cx0, cy0 = int(C.W * 0.1), int(C.H * 0.1)
                g = g[cy0:cy0 + int(C.H * 0.8), cx0:cx0 + int(C.W * 0.8)]
                g = contrast(g, 1.2); g = saturation(g, 1.2); g = hue_shift(g, 18)
                g = cv2.resize(g, (1280, 720), interpolation=cv2.INTER_AREA)
                g[720 - 100:720 - 10, 1280 - 270:1280 - 10] = 255
                g[720 - 70:720 - 40, 1280 - 250:1280 - 60] = 40
                return g
            return map_gen(combo, C.read_frames_iter(wm_mp4, C.W, C.H)), 1280, 720, 23
        raise ValueError(v)

    for v in variants:
        import torch
        torch.cuda.empty_cache()  # 变体间清缓存,防显存累积
        g = gen_variant(v)
        if isinstance(g, tuple) and g[0] == "crop":
            _, area = g
            cw, ch = int(C.W * area ** 0.5) // 2 * 2, int(C.H * area ** 0.5) // 2 * 2
            k = 3  # 3 个随机位置实例,每个覆盖 1/3 帧
            accs_all, covs_all = [], []
            for inst in range(k):
                s = rng
                x0 = int(s.integers(0, C.W - cw + 1)) // 2 * 2
                y0 = int(s.integers(0, C.H - ch + 1)) // 2 * 2
                lo, hi = inst * n // k, (inst + 1) * n // k
                cnt = hi - lo
                it = C.read_frames_iter(wm_mp4, C.W, C.H)
                it = (f for i, f in enumerate(it) if lo <= i < hi)
                it = crop_gen(it, cnt, x0, y0, cw, ch)
                t0 = time.perf_counter()
                accs, covs, dms = C.decode_batch_stats(wam, it, cnt, msg)
                accs_all.append(accs); covs_all.append(covs)
                print(f"    {v} inst{inst} rect=({x0},{y0},{cw},{ch}) exact={(accs == 1).mean():.1%} cov={covs.mean():.3f}", flush=True)
            accs_all = np.concatenate(accs_all); covs_all = np.concatenate(covs_all)
            r = dict(name=v, n=len(accs_all), exact=float((accs_all == 1).mean()),
                     ge31=float((accs_all >= 31 / 32).mean()), acc_mean=float(accs_all.mean()),
                     acc_p5=float(np.percentile(accs_all, 5)), cov_mean=float(covs_all.mean()),
                     decode_ms=-1, wall_s=time.perf_counter() - t0)
            results.append(r)
            print(f"  {v:<26} exact={r['exact']:6.1%}  >=31/32={r['ge31']:6.1%}  acc={r['acc_mean']:.4f}  cov={r['cov_mean']:.3f}", flush=True)
            continue
        if isinstance(g, tuple) and g[0] == "pip":
            other = C.read_frames(C.one_video(), 12000, 60) or C.read_frames(C.one_video(), 0, 60)
            k = 1
            def pip_gen():
                for i, f in enumerate(C.read_frames_iter(wm_mp4, C.W, C.H)):
                    yield pip_paste(f, other[i % len(other)])
            t0 = time.perf_counter()
            accs, covs, dms = C.decode_batch_stats(wam, pip_gen(), n, msg)
            r = dict(name="pip25", n=n, exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()),
                     acc_mean=float(accs.mean()), acc_p5=float(np.percentile(accs, 5)), cov_mean=float(covs.mean()),
                     decode_ms=float(dms), wall_s=time.perf_counter() - t0)
            results.append(r)
            print(f"  {'pip25':<26} exact={r['exact']:6.1%}  >=31/32={r['ge31']:6.1%}  acc={r['acc_mean']:.4f}  cov={r['cov_mean']:.3f}", flush=True)
            continue
        if isinstance(g, tuple) and g[0] == "occl":
            k = 3
            accs_all, covs_all = [], []
            for inst in range(k):
                w0, h0 = 576, 360  # 10% 面积
                x0 = int(rng.integers(0, C.W - w0 + 1)) // 2 * 2
                y0 = int(rng.integers(0, C.H - h0 + 1)) // 2 * 2
                rect = (x0, y0, w0, h0)
                it = map_gen(lambda f, r=rect: occlude(f, r, rng), C.read_frames_iter(wm_mp4, C.W, C.H))
                accs, covs, _ = C.decode_batch_stats(wam, it, n, msg)
                accs_all.append(accs); covs_all.append(covs)
                print(f"    occl10 inst{inst} rect={rect} exact={(accs == 1).mean():.1%}", flush=True)
            accs_all = np.concatenate(accs_all); covs_all = np.concatenate(covs_all)
            r = dict(name="occl10", n=len(accs_all), exact=float((accs_all == 1).mean()),
                     ge31=float((accs_all >= 31 / 32).mean()), acc_mean=float(accs_all.mean()),
                     acc_p5=float(np.percentile(accs_all, 5)), cov_mean=float(covs_all.mean()),
                     decode_ms=-1, wall_s=0)
            results.append(r)
            print(f"  {'occl10':<26} exact={r['exact']:6.1%}  >=31/32={r['ge31']:6.1%}  acc={r['acc_mean']:.4f}  cov={r['cov_mean']:.3f}", flush=True)
            continue

        if isinstance(g, tuple) and g[0] == "gen2":
            t1 = TMP / f"{args.tag}_gen2_pass1.mp4"
            C.encode_frames(C.read_frames_iter(wm_mp4, C.W, C.H), t1, 23)
            t2 = TMP / f"{args.tag}_gen2_pass2.mp4"
            C.encode_frames(C.read_frames_iter(t1, C.W, C.H), t2, 23)
            report(wam, "gen2", C.read_frames_iter(t2, C.W, C.H), n, msg, results)
            continue
        if isinstance(g, tuple) and g[0] == "fps30":
            t1 = TMP / f"{args.tag}_fps30.mp4"
            C.encode_frames(g[1], t1, 23, fps=30)
            report(wam, "fps30", C.read_frames_iter(t1, C.W, C.H), n // 2, msg, results)
            continue
        it, w, h, crf = g
        if w is None:  # 旋转: 推断展开画布尺寸
            probe = rotate(np.zeros((C.H, C.W, 3), np.uint8), {"rot5": 5, "rot15": 15, "rot60": 60}[v])
            h, w = probe.shape[:2]
        if crf is not None:
            tmp = TMP / f"{args.tag}_{v}.mp4"
            C.encode_frames(it, tmp, crf, w=w, h=h)
            it = C.read_frames_iter(tmp, w, h)
        report(wam, v, it, n, msg, results)

    (segdir / f"attacks_{args.suite}{args.suffix}.json").write_text(
        json.dumps(dict(tag=args.tag, suite=args.suite, results=results), indent=2), encoding="utf-8")
    import torch
    torch.cuda.empty_cache()
    print("[s2] 完成", flush=True)


if __name__ == "__main__":
    main()
