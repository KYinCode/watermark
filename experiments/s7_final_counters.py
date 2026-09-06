# -*- coding: utf-8 -*-
"""s7: 四项对策最终验证。
1) rot60: 修正中心裁剪(画布可能窄于1920)后反向旋转,全段。
2) mirror+crop: 镜像重试解码(翻回原朝向),码本裁决。
3) combo: 同上镜像重试。
4) canvas70: mask>0.9 最大连通域 bbox,逐帧裁剪二次解码。
"""
import json

import cv2
import numpy as np
import torch

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C

TMP = C.OUT / "tmp"


def inv_rot_fixed(f, angle):
    """反向旋转,裁剪不越界"""
    h, w = f.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    r = cv2.warpAffine(f, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    ch, cw = min(C.H, h), min(C.W, w)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return r[y0:y0 + ch, x0:x0 + cw]


def decode_one(wam, frame, msg_np):
    accs, covs, _ = C.decode_batch_stats(wam, [frame], 1, msg_np, report_ms=False)
    return float(accs[0]), float(covs[0])


def main():
    msg_np = C.wm_msg_bits()
    wam = C.load_wam(scaling_w=2.0)
    out = {}

    # 1) rot60 反向旋转(修正裁剪)
    accs, _, _ = C.decode_batch_stats(
        wam, (inv_rot_fixed(f, -60) for f in C.read_frames_iter_auto(TMP / "sw20_rot60.mp4")),
        1, msg_np, report_ms=False)
    out["rot60_invrot_fixed"] = dict(exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()),
                                     acc=float(accs.mean()))
    print(f"  rot60_invrot_fixed   exact={out['rot60_invrot_fixed']['exact']:6.1%} "
          f"ge31={out['rot60_invrot_fixed']['ge31']:6.1%} acc={out['rot60_invrot_fixed']['acc']:.4f}", flush=True)

    # 2/3) 镜像重试
    for name in ("dbg_a_mirror_crop", "dbg_g_full_combo"):
        src = TMP / f"{name}.mp4"
        if not src.exists():
            continue
        accs = []
        for f in C.read_frames_iter_auto(src):
            a, _ = decode_one(wam, np.ascontiguousarray(f[:, ::-1]), msg_np)
            accs.append(a)
        accs = np.array(accs)
        out[name + "_mirrorretry"] = dict(exact=float((accs == 1).mean()),
                                          ge31=float((accs >= 31 / 32).mean()), acc=float(accs.mean()))
        print(f"  {name}_mirrorretry exact={out[name + '_mirrorretry']['exact']:6.1%} "
              f"ge31={out[name + '_mirrorretry']['ge31']:6.1%} acc={out[name + '_mirrorretry']['acc']:.4f}", flush=True)

    # 4) canvas70 mask>0.9 CC bbox
    accs = []
    for f in C.read_frames_iter_auto(TMP / "sw20_canvas70.mp4"):
        x = C.norm_frames(f[None])
        with torch.no_grad():
            preds = wam.detect(x)["preds"]
        mask = torch.sigmoid(preds[0, 0]).cpu().numpy()
        sel = (mask > 0.9).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(sel, 8)
        crop = None
        if n > 1:
            k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            x0, y0 = stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_TOP]
            w0, h0 = stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT]
            fh, fw = f.shape[:2]
            gx0, gy0 = int(x0 / 256 * fw), int(y0 / 256 * fh)
            gx1, gy1 = int((x0 + w0) / 256 * fw), int((y0 + h0) / 256 * fh)
            if gx1 - gx0 >= 48 and gy1 - gy0 >= 48:
                crop = f[gy0:gy1, gx0:gx1]
        if crop is None:
            accs.append(0.0)
            continue
        a, _ = decode_one(wam, crop, msg_np)
        accs.append(a)
    accs = np.array(accs)
    out["canvas70_mask09_cc"] = dict(exact=float((accs == 1).mean()), ge31=float((accs >= 31 / 32).mean()),
                                     acc=float(accs.mean()))
    print(f"  canvas70_mask09_cc   exact={out['canvas70_mask09_cc']['exact']:6.1%} "
          f"ge31={out['canvas70_mask09_cc']['ge31']:6.1%} acc={out['canvas70_mask09_cc']['acc']:.4f}", flush=True)

    (C.OUT / "seg_sw20" / "s7.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("[s7] 完成", flush=True)


if __name__ == "__main__":
    main()
