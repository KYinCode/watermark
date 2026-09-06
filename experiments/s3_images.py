# -*- coding: utf-8 -*-
"""s3: 2 张源图打水印 + 1:1 解码 + 随机裁剪(25%/36%)定位解码。"""
import json

import cv2
import numpy as np
import torch

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C

OUTDIR = C.OUT / "images"
OUTDIR.mkdir(parents=True, exist_ok=True)


def main():
    import os
    d = C.PROJ / "DeepSeek+DSH"
    pngs = [p for p in d.iterdir() if p.suffix.lower() == ".png"]
    wam = C.load_wam(scaling_w=2.0)
    msg_np = C.wm_msg_bits()
    msg = torch.from_numpy(msg_np).float().unsqueeze(0).cuda()
    results = []
    for p in pngs:
        img = cv2.cvtColor(C.imread_unicode(p), cv2.COLOR_BGR2RGB)  # RGB
        h, w = img.shape[:2]
        with torch.no_grad():
            out = wam.embed(C.norm_frames(img[None]), msg)
        wm = C.unnorm_to_uint8(out["imgs_w"])[0]
        psnr = C.psnr_db(img, wm)
        C.imwrite_unicode(OUTDIR / (p.stem + "_wm.png"), cv2.cvtColor(wm, cv2.COLOR_RGB2BGR))
        accs, covs, _ = C.decode_batch_stats(wam, [wm], 1, msg_np)
        row = dict(img=p.name, size=f"{w}x{h}", psnr=round(psnr, 2),
                   exact_1to1=float(accs[0] == 1), acc_1to1=float(accs[0]), cov_1to1=float(covs[0]),
                   crops=[])
        rng = np.random.default_rng(1)
        for area in (0.25, 0.36):
            cw, ch = int(w * area ** 0.5) // 2 * 2, int(h * area ** 0.5) // 2 * 2
            accs_c, covs_c = [], []
            for inst in range(4):
                x0 = int(rng.integers(0, w - cw + 1)) // 2 * 2
                y0 = int(rng.integers(0, h - ch + 1)) // 2 * 2
                crop = wm[y0:y0 + ch, x0:x0 + cw]
                a, c, _ = C.decode_batch_stats(wam, [crop], 1, msg_np)
                accs_c.append(float(a[0])); covs_c.append(float(c[0]))
            row["crops"].append(dict(area=area, exact=float(np.mean(np.array(accs_c) == 1)),
                                     acc_mean=float(np.mean(accs_c)), cov_mean=float(np.mean(covs_c))))
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (OUTDIR / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[s3] 完成", flush=True)


if __name__ == "__main__":
    main()
