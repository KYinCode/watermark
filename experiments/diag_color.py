# -*- coding: utf-8 -*-
"""诊断成品色偏:白区泛黄/深蓝泛紫,来自水印残差还是编码?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import torch

import common as C


def region_stats(orig, prod):
    o = orig.astype(np.float32)
    q = prod.astype(np.float32)
    d = q - o
    white = (o.min(axis=2) > 200)
    blue = (o[:, :, 2] > 60) & (o[:, :, 0] < 90) & (o[:, :, 1] < 90)
    rows = []
    for name, m in (("全区", np.ones(o.shape[:2], bool)), ("白区", white), ("深蓝区", blue)):
        if m.sum() < 100:
            rows.append((name, None))
            continue
        rows.append((name, dict(cover=round(float(m.mean()), 3),
                                dR=round(float(d[:, :, 0][m].mean()), 2),
                                dG=round(float(d[:, :, 1][m].mean()), 2),
                                dB=round(float(d[:, :, 2][m].mean()), 2),
                                absmax=round(float(np.abs(d).max()), 1))))
    return rows


def show(title, rows):
    print(f"  {title}:")
    for name, s in rows:
        if s is None:
            print(f"    {name}: (区域过小)")
        else:
            print(f"    {name}: 覆盖{s['cover']:.0%}  ΔR={s['dR']:+.2f} ΔG={s['dG']:+.2f} ΔB={s['dB']:+.2f}  |Δ|max={s['absmax']}", flush=True)


def main():
    product = C.OUTPUT / "DeepSeek+DSH_已加水印_v2.mp4"
    src = C.one_video()
    wam = C.load_wam(scaling_w=2.0)
    msg1 = torch.from_numpy(C.wm_msg_bits()).float().unsqueeze(0).cuda()
    mean_t = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std_t = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)

    for label, idx in (("深蓝网格段 帧5100", 5100), ("白底UI段 帧17900", 17900)):
        orig = C.read_frames(src, idx, 1)[0]
        prod = C.read_frames(product, idx, 1)[0]
        print(f"[{label}]", flush=True)
        show("A. 成品 vs 原始(水印+编码,用户所见)", region_stats(orig, prod))

        # 重嵌该帧,取编码前的水印 delta
        with torch.no_grad():
            x = C.norm_frames(orig[None])
            out = wam.embed(x, msg1)
            pre = C.unnorm_to_uint8(out["imgs_w"])[0]
        show("B. 编码前水印帧 vs 原始(纯水印)", region_stats(orig, pre))
        show("C. 成品 vs 编码前水印帧(纯编码影响)", region_stats(pre, prod))


if __name__ == "__main__":
    main()
