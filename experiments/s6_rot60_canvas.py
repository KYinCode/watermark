# -*- coding: utf-8 -*-
"""s6: rot60 与 canvas70 的对策实验(子样 60 帧)。
rot60: (a)无重编码往返旋转→验证混叠假设 (b)2x超采样反向旋转 (c)三次插值
canvas70: (d)mask>0.9 阈值 + 最大连通域 bbox (e)其上再缩边 3%
"""
import json

import cv2
import numpy as np

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C
import s5_refine as S5

TMP = C.OUT / "tmp"
SUB = 60


def inv_rot(f, angle, interp=cv2.INTER_LINEAR, up=1):
    h, w = f.shape[:2]
    if up > 1:
        f = cv2.resize(f, (w * up, h * up), interpolation=cv2.INTER_LINEAR)
        h, w = f.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    r = cv2.warpAffine(f, M, (w, h), flags=interp, borderValue=(0, 0, 0))
    y0, x0 = (h - C.H * up) // 2, (w - C.W * up) // 2
    r = r[y0:y0 + C.H * up, x0:x0 + C.W * up]
    if up > 1:
        r = cv2.resize(r, (C.W, C.H), interpolation=cv2.INTER_AREA)
    return r


def main():
    import torch
    msg_np = C.wm_msg_bits()
    wam = C.load_wam(scaling_w=2.0)
    out = {}

    def decode_gen(it, name, n):
        accs, covs, _ = C.decode_batch_stats(wam, it, n, msg_np, report_ms=False)
        r = dict(exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()), acc=float(accs.mean()))
        out[name] = r
        print(f"  {name:<28} exact={r['exact']:6.1%} ge31={r['ge31']:6.1%} acc={r['acc']:.4f}", flush=True)
        return r

    # --- rot60 ---
    src = TMP / "sw20_rot60.mp4"
    frames = []
    for i, f in enumerate(C.read_frames_iter_auto(src)):
        if i >= SUB:
            break
        frames.append(f)

    # (a) 无重编码往返: 原水印帧 rotate+60 再 -60,验证双线性混叠是否毁灭性
    wmframes = C.read_frames(C.OUT / "seg_sw20" / "wm.mp4", 100, SUB)
    decode_gen((inv_rot(S2_rotate(f, 60), -60) for f in wmframes), "a_roundtrip_noenc", SUB)

    # (b) 攻击帧 2x 超采样反向旋转
    decode_gen((inv_rot(f, -60, up=2) for f in frames), "b_invrot_up2", SUB)

    # (c) 三次插值反向旋转
    decode_gen((inv_rot(f, -60, interp=cv2.INTER_CUBIC) for f in frames), "c_invrot_cubic", SUB)

    # --- canvas70 ---
    src = TMP / "sw20_canvas70.mp4"

    def bbox_hi(f):
        x = C.norm_frames(f[None])
        with torch.no_grad():
            preds = wam.detect(x)["preds"]
        mask = torch.sigmoid(preds[0, 0]).cpu().numpy()
        sel = (mask > 0.9).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(sel, 8)
        if n <= 1:
            return None, f
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x0, y0 = stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_TOP]
        w0, h0 = stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT]
        return (x0, y0, x0 + w0, y0 + h0), f

    def bbox_hi_shrink(f, shrink=0.03):
        bb, fr = bbox_hi(f)
        if bb is None:
            return fr[0:48, 0:48]
        x0, y0, x1, y1 = bb
        mx, my = int((x1 - x0) * shrink), int((y1 - y0) * shrink)
        fh, fw = fr.shape[:2]
        gx0, gx1 = int(x0 / 256 * fw) + mx, int(x1 / 256 * fw) - mx
        gy0, gy1 = int(y0 / 256 * fh) + my, int(y1 / 256 * fh) - my
        return fr[gy0:gy1, gx0:gx1]

    decode_gen((bbox_hi_shrink(f) for f in C.read_frames_iter_auto(src)), "d_canvas70_mask09_cc", 660)
    decode_gen((bbox_hi_shrink(f, 0.06) for f in C.read_frames_iter_auto(src)), "e_canvas70_mask09_shr6", 660)

    (C.OUT / "seg_sw20" / "s6.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("[s6] 完成", flush=True)


def S2_rotate(f, angle):
    import s2_attacks as S2
    return S2.rotate(f, angle)


if __name__ == "__main__":
    main()
