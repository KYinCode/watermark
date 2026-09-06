# -*- coding: utf-8 -*-
"""s8: canvas70 终极对策——定位内容框后还原到 1920x1080 再解码。
a) 已知几何上界: 直接中央裁 1344x756 解码
b) 中央裁 1344x756 放大回 1920x1080 解码(预期 winner)
c) b 的全自动版: mask>0.5 最大连通域 bbox(收缩3%)裁剪后放大回 1920x1080
"""
import json

import cv2
import numpy as np
import torch

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C

TMP = C.OUT / "tmp"


def main():
    msg_np = C.wm_msg_bits()
    wam = C.load_wam(scaling_w=2.0)
    out = {}
    src = TMP / "sw20_canvas70.mp4"

    def gen_a():
        for f in C.read_frames_iter_auto(src):
            yield f[162:918, 288:1632]  # (1080-756)/2, (1920-1344)/2

    def gen_b():
        for f in gen_a():
            yield cv2.resize(f, (C.W, C.H), interpolation=cv2.INTER_LANCZOS4)

    def gen_c():
        for f in C.read_frames_iter_auto(src):
            x = C.norm_frames(f[None])
            with torch.no_grad():
                preds = wam.detect(x)["preds"]
            mask = torch.sigmoid(preds[0, 0]).cpu().numpy()
            sel = (mask > 0.5).astype(np.uint8)
            n, lab, stats, _ = cv2.connectedComponentsWithStats(sel, 8)
            if n <= 1:
                yield np.zeros((48, 48, 3), np.uint8)
                continue
            k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            x0, y0 = stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_TOP]
            w0, h0 = stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT]
            mx, my = int(w0 * 0.03), int(h0 * 0.03)
            fh, fw = f.shape[:2]
            gx0, gy0 = int((x0 + mx) / 256 * fw), int((y0 + my) / 256 * fh)
            gx1, gy1 = int((x0 + w0 - mx) / 256 * fw), int((y0 + h0 - my) / 256 * fh)
            crop = f[gy0:gy1, gx0:gx1]
            if crop.shape[0] < 48 or crop.shape[1] < 48:
                yield np.zeros((48, 48, 3), np.uint8)
                continue
            yield cv2.resize(crop, (C.W, C.H), interpolation=cv2.INTER_LANCZOS4)

    for name, it in (("a_known_geom_native", gen_a()),
                     ("b_known_geom_upscale", gen_b()),
                     ("c_mask_cc_upscale", gen_c())):
        accs, _, _ = C.decode_batch_stats(wam, it, 1, msg_np, report_ms=False)
        out[name] = dict(exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()), acc=float(accs.mean()))
        print(f"  {name:<24} exact={out[name]['exact']:6.1%} ge31={out[name]['ge31']:6.1%} acc={out[name]['acc']:.4f}",
              flush=True)

    (C.OUT / "seg_sw20" / "s8.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("[s8] 完成", flush=True)


if __name__ == "__main__":
    main()
