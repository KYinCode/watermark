# -*- coding: utf-8 -*-
"""s9: 解码端补偿搜索实验——救 hue-20 / bright-20 / combo(hd 段)。
A) hue-20(hd): 精确逆补偿(+36) 与 自动网格搜索 两种模式
B) combo(hd): {identity,mirror} × hue{0,±6,±12,±18,±24} 网格
C) bright-20(好段): 精确逆补偿(+51) 与 自动网格
产物: exp/out/seg_sw20/s9_results.json(实际写 验收数据/s9_results.json)
"""
import json

import cv2
import numpy as np

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C
import s2_attacks as S

TMP = C.OUT / "verify_tmp"


def hue_shift(f, units):
    hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV).astype(np.int16)
    hsv[:, :, 0] = (hsv[:, :, 0] + units) % 180
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)


def brightness(f, delta):
    return np.clip(f.astype(np.int16) + delta, 0, 255).astype(np.uint8)


def grid_decode(wam, it, n, msg_np, transforms, batch_note=""):
    """transforms: list[(name, fn)];逐变换全段解码,返回每变换的 exact(帧级取最优)"""
    out = {}
    per_frame_best = np.zeros(n)
    for name, fn in transforms:
        accs, covs, _ = C.decode_batch_stats(wam, (fn(f) for f in it()), n, msg_np, report_ms=False)
        out[name] = dict(exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()),
                         acc=float(accs.mean()))
        per_frame_best = np.maximum(per_frame_best, (accs == 1).astype(float))
        print(f"    {name:<22} exact={out[name]['exact']:6.1%} ge31={out[name]['ge31']:6.1%}", flush=True)
    out["_best_of_grid"] = float(per_frame_best.mean())
    print(f"    {'网格逐帧最优':<22} exact={out['_best_of_grid']:6.1%}", flush=True)
    return out


def main():
    msg_np = C.wm_msg_bits()
    wam = C.load_wam(scaling_w=2.0)
    res = {}

    # ---- A) hue -20% (hd 段, 帧内直接作用在成品段上, 与 verify 相同) ----
    print("[s9] A) hue-20 @ seg17800", flush=True)
    prod17800 = TMP / "prod_seg17800.mp4"
    shifts = [0, 12, 24, 36, 48, 60, 72, -12, -24, -48, -60, -72]
    res["A_hue_neg20_hd"] = grid_decode(
        wam, lambda: (hue_shift(f, -36) for f in C.read_frames_iter(prod17800, C.W, C.H)), 660, msg_np,
        [(f"hue_inv{s:+d}" if s else "none", (lambda s=s: (lambda f: hue_shift(f, s) if s else f))()) for s in shifts])

    # ---- B) combo (hd 段) ----
    print("[s9] B) combo @ seg17800", flush=True)
    combo_src = TMP / "v_seg17800_combo.mp4"
    frames_iter = lambda: C.read_frames_iter_auto(combo_src)
    tr = []
    for m in (False, True):
        for s in (0, -6, -12, -18, -24, 6, 12):
            name = f"{'mirror' if m else 'asIs'}_hue{s:+d}"

            def mk(m=m, s=s):
                def fn(f):
                    if m:
                        f = np.ascontiguousarray(f[:, ::-1])
                    if s:
                        f = hue_shift(f, s)
                    return f
                return fn
            tr.append((name, mk()))
    res["B_combo_hd"] = grid_decode(wam, frames_iter, 660, msg_np, tr)

    # ---- C) bright -20% (好段) ----
    print("[s9] C) bright-20 @ seg5000", flush=True)
    prod5000 = TMP / "prod_seg5000.mp4"
    deltas = [0, 13, 26, 39, 51]
    res["C_bright_neg20_good"] = grid_decode(
        wam, lambda: (brightness(f, -51) for f in C.read_frames_iter(prod5000, C.W, C.H)), 660, msg_np,
        [(f"bright_inv{d:+d}" if d else "none", (lambda d=d: (lambda f: brightness(f, d) if d else f))()) for d in deltas])

    (C.PROJ / "验收数据" / "s9_results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print("[s9] 完成", flush=True)


if __name__ == "__main__":
    main()
