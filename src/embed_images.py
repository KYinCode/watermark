# -*- coding: utf-8 -*-
"""图片成品嵌入(scaling_w=2.5,调档实验选定)。由 run_all.py 调用,也可单独运行。"""
import argparse
import json
import logging
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

import common as C
import wmlog

SCALING_W = 2.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-file", type=str, default=None,
                    help="日志文件(可选):事件流水带时间戳写入该文件,控制台输出不变")
    args = ap.parse_args()
    if args.log_file:
        wmlog.setup_cli(args.log_file)
    log = logging.getLogger("wm.cli.embed_images")

    wam = C.load_wam(scaling_w=SCALING_W)
    msg_np = C.wm_msg_bits()
    msg = torch.from_numpy(msg_np).float().unsqueeze(0).cuda()
    res = []
    for p in C.data_images():
        img = cv2.cvtColor(C.imread_unicode(p), cv2.COLOR_BGR2RGB)
        h, w = img.shape[:2]
        with torch.no_grad():
            out = wam.embed(C.norm_frames(img[None]), msg)
        wm = C.unnorm_to_uint8(out["imgs_w"])[0]
        dst = C.out_name(p.stem, ".png")  # 成品统一无损 PNG,重名 _v2.._v99(与引擎/视频版共用)
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
        log.info("图片嵌入完成 %s -> %s psnr=%s exact=%s crop25=%s",
                 p.name, dst.name, res[-1]["psnr"], res[-1]["exact_1to1"], res[-1]["crop25_exact"])
    (C.OUTPUT / "成品图片_meta.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[images-final] 完成", flush=True)
    log.info("图片嵌入全部完成,共 %d 张", len(res))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("wm.cli.embed_images").exception("运行失败")
        raise SystemExit(1)
