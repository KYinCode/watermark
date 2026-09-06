# -*- coding: utf-8 -*-
"""s4: combo 失败定位——逐个拆解组合元素,220 帧子段快速测试。"""
import json

import cv2
import numpy as np

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C
import s2_attacks as S

TMP = C.OUT / "tmp"
SUB = 220


def run_variant(wam, name, fn, msg_np, results, crf=23, w=C.W, h=C.H, count=SUB):
    it = (fn(f) for f in C.read_frames_iter(C.OUT / "seg_sw20" / "wm.mp4", C.W, C.H))
    if crf is not None:
        tmp = TMP / f"dbg_{name}.mp4"
        C.encode_frames(it, tmp, crf, w=w, h=h)
        it = C.read_frames_iter(tmp, w, h)
    accs, covs, _ = C.decode_batch_stats(wam, it, count, msg_np, report_ms=False)
    exact = float((accs == 1).mean())
    results[name] = dict(exact=exact, acc=float(accs.mean()), cov=float(covs.mean()))
    print(f"  {name:<34} exact={exact:6.1%} acc={accs.mean():.4f} cov={covs.mean():.3f}", flush=True)


def main():
    import json
    import torch

    msg_np = C.wm_msg_bits()
    wam = C.load_wam(scaling_w=2.0)
    results = {}

    def mirror(f):
        return f[:, ::-1].copy()

    def crop80(f):
        x0, y0 = int(C.W * 0.1), int(C.H * 0.1)
        return f[y0:y0 + int(C.H * 0.8), x0:x0 + int(C.W * 0.8)]

    def color(f):
        f = S.contrast(f, 1.2); f = S.saturation(f, 1.2); f = S.hue_shift(f, 18)
        return f

    def to720(f):
        return cv2.resize(f, (1280, 720), interpolation=cv2.INTER_AREA)

    def sticker(f):
        f[720 - 100:720 - 10, 1280 - 270:1280 - 10] = 255
        f[720 - 70:720 - 40, 1280 - 250:1280 - 60] = 40
        return f

    run_variant(wam, "a_mirror_crop", lambda f: crop80(mirror(f)), msg_np, results, w=1536, h=864)
    run_variant(wam, "b_crop_mirror", lambda f: mirror(crop80(f)), msg_np, results, w=1536, h=864)
    run_variant(wam, "c_mirror_crop_720", lambda f: to720(crop80(mirror(f))), msg_np, results, w=1280, h=720)
    run_variant(wam, "d_mirror_crop_color", lambda f: color(crop80(mirror(f))), msg_np, results, w=1536, h=864)
    run_variant(wam, "e_color_crop_720", lambda f: to720(color(crop80(f))), msg_np, results, w=1280, h=720)
    run_variant(wam, "f_mirror_crop_color_720", lambda f: to720(color(crop80(mirror(f)))), msg_np, results, w=1280, h=720)
    run_variant(wam, "g_full_combo", lambda f: sticker(to720(color(crop80(mirror(f))))), msg_np, results, w=1280, h=720)

    # 诊断1: 无重编码直解,排除 x264 影响
    run_variant(wam, "h_mirror_crop_noenc", lambda f: crop80(mirror(f)), msg_np, results, crf=None, w=1536, h=864)
    # 诊断2: 裁剪比例敏感性(均无重编码)
    for frac in (0.9, 0.7, 0.6):
        def cf(f, fr=frac):
            x0, y0 = int(C.W * (1 - fr) / 2), int(C.H * (1 - fr) / 2)
            return mirror(f)[y0:y0 + int(C.H * fr), x0:x0 + int(C.W * fr)]
        run_variant(wam, f"i_mirror_crop{int(frac*100)}", cf, msg_np, results, crf=None,
                    w=int(C.W * frac) // 2 * 2, h=int(C.H * frac) // 2 * 2)
    # 诊断3: 错误位是否跨帧一致(系统误码 vs 随机误码)
    it = (crop80(mirror(f)) for f in C.read_frames_iter(C.OUT / "seg_sw20" / "wm.mp4", C.W, C.H))
    softs = []
    for i, f in enumerate(it):
        if i >= 40:
            break
        x = C.norm_frames(f[None])
        with torch.no_grad():
            preds = wam.detect(x)["preds"]
        mask = torch.sigmoid(preds[0, 0])
        bits = preds[0, 1:]
        sel = mask > 0.5
        softs.append(bits[:, sel].mean(dim=1).cpu().numpy())
    softs = np.stack(softs)
    hard = (softs > 0).astype(np.float32)
    stability = (hard == hard[0]).mean()
    true = msg_np
    print(f"  [diag] 前40帧硬位与第0帧一致率={stability:.3f}  与真值一致率={(hard==true).mean():.3f}  "
          f"逐位均值min={hard.mean(0).min():.2f} max={hard.mean(0).max():.2f}", flush=True)
    results["diag_bit_stability_vs_frame0"] = float(stability)
    results["diag_bit_agree_vs_true"] = float((hard == true).mean())

    (C.OUT / "seg_sw20" / "combo_debug.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("[s4] 完成", flush=True)


if __name__ == "__main__":
    main()
