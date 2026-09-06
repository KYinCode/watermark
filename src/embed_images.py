# -*- coding: utf-8 -*-
"""图片成品嵌入(scaling_w=2.5,调档实验选定)。由 run_all.py 调用,也可单独运行。"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

import common as C

SCALING_W = 2.5


def main():
    wam = C.load_wam(scaling_w=SCALING_W)
    msg_np = C.wm_msg_bits()
    msg = torch.from_numpy(msg_np).float().unsqueeze(0).cuda()
    res = []
    for p in C.data_images():
        if p.suffix.lower() != ".png":
            continue
        img = cv2.cvtColor(C.imread_unicode(p), cv2.COLOR_BGR2RGB)
        h, w = img.shape[:2]
        with torch.no_grad():
            out = wam.embed(C.norm_frames(img[None]), msg)
        wm = C.unnorm_to_uint8(out["imgs_w"])[0]
        dst = C.OUTPUT / (p.stem + "_已加水印.png")
        C.imwrite_unicode(dst, cv2.cvtColor(wm, cv2.COLOR_RGB2BGR))
        a1, _, _ = C.decode_batch_stats(wam, [wm], 1, msg_np, report_ms=False)
        rng = np.random.default_rng(11)
        accs = []
        for _ in range(20):
            cw, ch = int(w * 0.5) // 2 * 2, int(h * 0.5) // 2 * 2
            x0 = int(rng.integers(0, w - cw + 1)) // 2 * 2
            y0 = int(rng.integers(0, h - ch + 1)) // 2 * 2
            a, _, _ = C.decode_batch_stats(wam, [wm[y0:y0 + ch, x0:x0 + cw]], 1, msg_np, report_ms=False)
            accs.append(a[0])
        res.append(dict(img=p.name, out=dst.name, scaling_w=SCALING_W,
                        psnr=round(C.psnr_db(img, wm), 2), exact_1to1=bool(a1[0] == 1),
                        crop25_exact=float(np.mean(np.array(accs) == 1))))
        print(json.dumps(res[-1], ensure_ascii=False), flush=True)
    (C.OUTPUT / "图片_已加水印_meta.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[images-final] 完成", flush=True)


if __name__ == "__main__":
    main()
