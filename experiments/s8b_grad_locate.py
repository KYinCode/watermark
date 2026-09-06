# -*- coding: utf-8 -*-
"""s8b: canvas70 自动定位终版——块梯度能量 Otsu 阈值 + 最大连通域(内容锐利/背景模糊)。"""
import json

import cv2
import numpy as np

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C

TMP = C.OUT / "tmp"


def grad_locate(frame, block=32):
    """返回内容区 bbox (x0,y0,x1,y1);块梯度能量 Otsu + 开运算 + 最大连通域"""
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    h, w = mag.shape
    hb, wb = h // block, w // block
    energy = mag[:hb * block, :wb * block].reshape(hb, block, wb, block).mean(axis=(1, 3))
    e8 = np.clip(energy / max(energy.max(), 1e-6) * 255, 0, 255).astype(np.uint8)
    _, bw = cv2.threshold(e8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=2)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    if n <= 1:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw_, bh_ = stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_TOP], stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT]
    return (x * block, y * block, min((x + bw_) * block, w), min((y + bh_) * block, h))


def main():
    msg_np = C.wm_msg_bits()
    wam = C.load_wam(scaling_w=2.0)
    out = {}
    src = TMP / "sw20_canvas70.mp4"

    accs = []
    rects = []
    for f in C.read_frames_iter_auto(src):
        bb = grad_locate(f)
        if bb is None:
            accs.append(0.0)
            continue
        x0, y0, x1, y1 = bb
        rects.append(bb)
        crop = f[y0:y1, x0:x1]
        accs1, _, _ = C.decode_batch_stats(wam, [crop], 1, msg_np, report_ms=False)
        accs.append(accs1[0])
    accs = np.array(accs)
    rects = np.array(rects)
    out["canvas70_grad_locate"] = dict(
        exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()), acc=float(accs.mean()),
        rect_median=[int(v) for v in np.median(rects, axis=0)] if len(rects) else None,
        rect_std=[float(v) for v in rects.std(axis=0)] if len(rects) else None)
    print(f"  canvas70_grad_locate exact={out['canvas70_grad_locate']['exact']:6.1%} "
          f"ge31={out['canvas70_grad_locate']['ge31']:6.1%} acc={out['canvas70_grad_locate']['acc']:.4f} "
          f"rect_med={out['canvas70_grad_locate']['rect_median']} (真值 [288,162,1632,918])", flush=True)

    (C.OUT / "seg_sw20" / "s8b.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("[s8b] 完成", flush=True)


if __name__ == "__main__":
    main()
