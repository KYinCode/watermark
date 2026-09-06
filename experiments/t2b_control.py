# -*- coding: utf-8 -*-
"""2b 对照实验(无水印,纯编码): 同一内容 x265 10bit crf14 vs x264 8bit crf14,
测深蓝/白区色偏 -> 判定色偏主因是位深还是 4:2:0 色度处理/编码器行为。纯 CPU。
结果: experiments/out/t2b_matrix/control.json
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import common as C

OUTD = C.OUT / "t2b_matrix"
N = 660
DIAG_LOCAL = 100
SEGS = [(5000, "A_深蓝网格"), (17800, "B_白底UI")]


def region_stats(orig, prod):
    o = orig.astype(np.float32)
    q = prod.astype(np.float32)
    d = q - o
    rows = {}
    for name, m in (("white", o.min(axis=2) > 200),
                    ("blue", (o[:, :, 2] > 60) & (o[:, :, 0] < 90) & (o[:, :, 1] < 90))):
        if m.sum() < 100:
            rows[name] = None
            continue
        rows[name] = dict(dR=round(float(d[:, :, 0][m].mean()), 2),
                          dG=round(float(d[:, :, 1][m].mean()), 2),
                          dB=round(float(d[:, :, 2][m].mean()), 2))
    return rows


def encode(frames_iter, path, codec):
    if codec == "x265_10bit":
        vf, cv_args = "scale=out_color_matrix=bt709,format=yuv420p10le", \
            ["-c:v", "libx265", "-preset", "medium", "-crf", "14", "-tag:v", "hvc1"]
    elif codec == "x264_10bit":
        vf, cv_args = "scale=out_color_matrix=bt709,format=yuv420p10le", \
            ["-c:v", "libx264", "-preset", "medium", "-crf", "14"]
    elif codec == "x264_8bit_crf10":
        vf, cv_args = "scale=out_color_matrix=bt709,format=yuv420p", \
            ["-c:v", "libx264", "-preset", "medium", "-crf", "10"]
    else:
        vf, cv_args = "scale=out_color_matrix=bt709,format=yuv420p", \
            ["-c:v", "libx264", "-preset", "medium", "-crf", "14"]
    p = subprocess.Popen(
        [C.FF, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{C.W}x{C.H}", "-r", str(C.FPS), "-i", "-",
         "-vf", vf, *cv_args, str(path)], stdin=subprocess.PIPE)
    for f in frames_iter:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    assert p.wait() == 0


def main():
    src = C.one_video()
    res = []
    for start, tag in SEGS:
        frames = C.read_frames(src, start, N)
        orig = frames[DIAG_LOCAL]
        for codec in ("x265_10bit", "x264_10bit", "x264_8bit", "x264_8bit_crf10"):
            out = OUTD / f"ctrl_seg{start}_{codec}.mp4"
            encode(iter(frames), out, codec)
            prod = C.read_frames(out, DIAG_LOCAL, 1)[0]
            st = region_stats(orig, prod)
            blue = st.get("blue")
            extra = None if blue is None else dict(
                vs_meanRG=round((blue["dR"] + blue["dG"]) / 2 - blue["dB"], 2),
                vs_R=round(blue["dR"] - blue["dB"], 2))
            white = st.get("white")
            res.append(dict(seg=tag, codec=codec, stats=st, extraB=extra,
                            white_dB=white["dB"] if white else None))
            out.unlink()
            print(f"[ctrl][{tag}][{codec}] 蓝={blue} extraB={extra} 白dB={res[-1]['white_dB']}",
                  flush=True)
        del frames
    (OUTD / "control.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print("[ctrl] 完成", flush=True)


if __name__ == "__main__":
    main()
