# -*- coding: utf-8 -*-
"""bench: 嵌入/解码批量基准,选安全最快的 batch。"""
import sys
import time

import numpy as np
import torch

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "src"))
import common as C


def main():
    n = 64
    frames = C.read_frames(C.one_video(), 5000, n)
    msg1 = torch.from_numpy(C.wm_msg_bits()).float().unsqueeze(0).cuda()

    print("== embed batch bench ==")
    for B in (8, 16, 24):
        wam = C.load_wam(scaling_w=2.0)
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        with torch.no_grad():
            for i in range(0, n, B):
                x = C.norm_frames(np.stack(frames[i:i + B]))
                wam.embed(x, msg1.repeat(x.shape[0], 1))
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        peak = torch.cuda.max_memory_allocated() / 2**30
        print(f"batch={B:>3}: {1000*dt/n:7.1f} ms/帧  peak={peak:.2f} GiB  外推全片 {dt/n*22478/60:.0f} min")
        del wam
        torch.cuda.empty_cache()

    print("== decode batch bench ==")
    wmf = C.read_frames(C.OUT / "seg_sw20" / "wm.mp4", 0, n)
    for B in (16, 32):
        wam = C.load_wam(scaling_w=2.0)
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        C.decode_batch_stats(wam, wmf, n, C.wm_msg_bits(), batch=B, report_ms=False)
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        peak = torch.cuda.max_memory_allocated() / 2**30
        print(f"batch={B:>3}: {1000*dt/n:7.1f} ms/帧  peak={peak:.2f} GiB")
        del wam
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
