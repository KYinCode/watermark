# -*- coding: utf-8 -*-
"""smoke: 验证 WAM 加载/嵌入/解码链路正确性(合成图,1 分钟内完成)。"""
import numpy as np
import torch

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C


def main():
    wam = C.load_wam(scaling_w=2.0)
    rng = np.random.default_rng(0)
    img = (np.tile(np.linspace(0, 255, 640, dtype=np.uint8), (360, 1))[..., None]
           * np.array([1.0, 0.7, 0.4])[None, None, :]).astype(np.uint8)
    img = np.stack([img] * 2)
    msg = torch.from_numpy(C.wm_msg_bits()).float().unsqueeze(0).cuda().repeat(2, 1)
    with torch.no_grad():
        out = wam.embed(C.norm_frames(img), msg)
    wm = C.unnorm_to_uint8(out["imgs_w"])
    for i in range(2):
        print(f"PSNR {C.psnr_db(img[i], wm[i]):.2f} dB, delta8bit max={int(np.abs(img[i].astype(int)-wm[i].astype(int)).max())}")
    accs, covs, _ = C.decode_batch_stats(wam, wm, 2, C.wm_msg_bits())
    print("decode bit_acc:", accs, "coverage:", covs)
    print("GPU:", torch.cuda.get_device_name(0), "mem peak GiB:", round(torch.cuda.max_memory_allocated() / 2**30, 2))


if __name__ == "__main__":
    main()
