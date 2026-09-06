# -*- coding: utf-8 -*-
"""s5: 两步式解码增强,针对 s2 失分项。
1) 旋转: 先在 60 帧子样上做角度搜索,再用最优角反向旋转+中心裁剪,全段解码。
2) 包边/画中画: 定位 mask 取 bbox 裁剪内容区,对裁剪区二次检测。
输入复用 s2 的中间产物 exp/out/tmp/sw20_*.mp4。
"""
import json

import cv2
import numpy as np

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C

TMP = C.OUT / "tmp"
SEGSUB = 60  # 角度搜索子样帧数


def inverse_rotate_crop(f, angle):
    """对攻击帧(已 expand 的画布)反向旋转 angle 并中心裁剪回 1920x1080"""
    h, w = f.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    r = cv2.warpAffine(f, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    y0, x0 = (h - C.H) // 2, (w - C.W) // 2
    return r[y0:y0 + C.H, x0:x0 + C.W]


def mask_bbox(mask_sel, mode="raw"):
    """256² mask -> 原图坐标 bbox。mode: raw=直接外接框; cc=腐蚀+最大连通域"""
    import cv2
    sel = mask_sel.astype(np.uint8)
    if mode == "cc":
        sel = cv2.erode(sel, np.ones((5, 5), np.uint8), iterations=2)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(sel, 8)
        if n <= 1:
            return None
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x, y, w, h = stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_TOP], stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT]
        return x, y, x + w, y + h
    ys, xs = np.where(sel > 0)
    if len(ys) == 0:
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def bbox_two_step(wam, frame, msg_np, mode="raw", min_frac=0.05):
    """一次检测取 mask bbox -> 裁剪 -> 二次检测;返回 (bit_acc, cov2)"""
    import torch
    x = C.norm_frames(frame[None])
    with torch.no_grad():
        preds = wam.detect(x)["preds"]
    mask = torch.sigmoid(preds[0, 0]).cpu().numpy()
    sel = mask > 0.5
    if sel.mean() < min_frac:
        return 0.0, float(sel.mean())
    bb = mask_bbox(sel, mode)
    if bb is None:
        return 0.0, float(sel.mean())
    x0, y0, x1, y1 = bb
    fh, fw = frame.shape[:2]
    gy0, gy1 = int(y0 / mask.shape[0] * fh), int(y1 / mask.shape[0] * fh)
    gx0, gx1 = int(x0 / mask.shape[1] * fw), int(x1 / mask.shape[1] * fw)
    crop = frame[gy0:gy1, gx0:gx1]
    if crop.shape[0] < 48 or crop.shape[1] < 48:
        return 0.0, float(sel.mean())
    accs, covs, _ = C.decode_batch_stats(wam, [crop], 1, msg_np, report_ms=False)
    return float(accs[0]), float(covs[0])


def angle_search(wam, path, msg_np, angles):
    """在子样帧上搜索最优反向角,返回 (best_angle, 各角命中率)"""
    it = C.read_frames_iter_auto(path)
    frames = []
    for i, f in enumerate(it):
        if i >= SEGSUB:
            break
        frames.append(f)
    votes = {}
    for a in angles:
        accs, _, _ = C.decode_batch_stats(wam, (inverse_rotate_crop(f, a) for f in frames),
                                          len(frames), msg_np, report_ms=False)
        votes[a] = int((accs == 1).sum())
    best = max(votes, key=votes.get)
    return best, votes


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="sw20")
    args = ap.parse_args()
    msg_np = C.wm_msg_bits()
    wam = C.load_wam(scaling_w=2.0)
    out = {}

    # ---- 1) 旋转角度搜索 + 全段验证 ----
    for v, angles in (("rot5", list(range(-8, 9))),
                      ("rot15", list(range(-18, 19, 2))),
                      ("rot60", list(range(48, 73, 2)) + list(range(-72, -47, 2)))):
        src = TMP / f"{args.tag}_{v}.mp4"
        if not src.exists():
            continue
        best, votes = angle_search(wam, src, msg_np, angles)
        top = sorted(votes.items(), key=lambda kv: -kv[1])[:5]
        print(f"[s5] {v} 角度搜索(子样{SEGSUB}帧): best={best}° top={top}", flush=True)
        it = (inverse_rotate_crop(f, best) for f in C.read_frames_iter_auto(src))
        accs, covs, dms = C.decode_batch_stats(wam, it, 1, msg_np, report_ms=False)
        out[v + "_twostep"] = dict(best_angle=best, exact=float((accs == 1).mean()),
                                   ge31=float((accs >= 31 / 32).mean()), acc=float(accs.mean()))
        print(f"[s5] {v} 反旋{best}°全段: exact={out[v+'_twostep']['exact']:.1%} "
              f">=31/32={out[v+'_twostep']['ge31']:.1%} acc={out[v+'_twostep']['acc']:.4f} ({dms:.0f} ms/帧)", flush=True)

    # ---- 2) 包边 bbox 两步解码(raw 与 腐蚀连通域 两种提纯) ----
    for v in ("canvas70", "canvas90", "pip25"):
        src = TMP / f"{args.tag}_{v}.mp4"
        if not src.exists():
            continue
        for mode in ("raw", "cc"):
            accs, covs = [], []
            for f in C.read_frames_iter_auto(src):
                a, c = bbox_two_step(wam, f, msg_np, mode=mode)
                accs.append(a); covs.append(c)
            accs = np.array(accs)
            out[f"{v}_bbox_{mode}"] = dict(exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()),
                                           acc=float(accs.mean()), cov2=float(np.mean(covs)))
            print(f"[s5] {v} bbox[{mode}]: exact={out[f'{v}_bbox_{mode}']['exact']:.1%} "
                  f">=31/32={out[f'{v}_bbox_{mode}']['ge31']:.1%} acc={out[f'{v}_bbox_{mode}']['acc']:.4f} "
                  f"cov2={out[f'{v}_bbox_{mode}']['cov2']:.3f}", flush=True)

    (C.OUT / f"seg_{args.tag}" / "refine.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("[s5] 完成", flush=True)


if __name__ == "__main__":
    main()
