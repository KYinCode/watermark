# -*- coding: utf-8 -*-
"""模拟录屏场景对水印提取率的影响 (v2: 修解码尺寸bug + 扩展场景)。

录屏链路 = 播放器渲染(可能缩放) → 屏幕捕获 → 录屏软件重编码(h264 crf~23)。
场景(每段300帧连续):
  A 1:1基线        成品直接解码提取(对照)
  B1 全屏1:1录屏   原尺寸 crf18 重编码(高画质录屏)
  B2 全屏1:1录屏   原尺寸 crf23 重编码(OBS默认档)
  C  缩放75%录屏   resize 1440x810 crf23, 解码后还原 1920x1080 再提取
  D  高强度d36版   GpuWaterMarkCore(d1=36,d2=20) 重嵌后 crf23 重编码再提取
段: 好段 5000- / 高动态段 17800-。
"""
import os
import subprocess
import warnings
from functools import partial
from multiprocessing import Pool

warnings.filterwarnings('ignore')
import cv2
import numpy as np

FF = r'C:\Environment\FFmpeg\FFmpeg_Builds\bin\ffmpeg.exe'
SRC = r'DeepSeek+DSH\DeepSeek+DSH_已加水印.mp4'
W, H, FPS = 1920, 1080, 60
WM_TEXT = '©Yin(Yin_Code/KYin_Code/KYin)版权所有|禁止搬运 Bili:3706940936948128 Douyin:Yin_Code'
SEGMENTS = [('好段', 5000, 300), ('高动态段', 17800, 300)]

_bwm_cache = {}


def get_bwm(d1, d2):
    key = (d1, d2)
    if key not in _bwm_cache:
        from blind_watermark import WaterMark
        from blind_watermark.version import bw_notes
        bw_notes.close()
        b = WaterMark(password_img=1, password_wm=1)
        b.bwm_core.d1, b.bwm_core.d2 = d1, d2
        _bwm_cache[key] = b
    return _bwm_cache[key]


def read_segment(start, n):
    p = subprocess.Popen(
        [FF, '-hide_banner', '-loglevel', 'error', '-i', SRC,
         '-vf', f'select=between(n\\,{start}\\,{start + n - 1})', '-vsync', '0',
         '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
        stdout=subprocess.PIPE)
    frames = []
    while len(frames) < n:
        buf = p.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        frames.append(np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3).copy())
    p.wait()
    assert len(frames) == n, f'只读到 {len(frames)}/{n} 帧'
    return frames


def rec_encode(frames, size_w, size_h, path, crf):
    p = subprocess.Popen(
        [FF, '-hide_banner', '-loglevel', 'error', '-y',
         '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{size_w}x{size_h}',
         '-r', str(FPS), '-i', '-',
         '-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf),
         '-pix_fmt', 'yuv420p', path],
        stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    assert p.wait() == 0, f'编码失败 {path}'


def rec_decode(path, n, w, h):
    p = subprocess.Popen(
        [FF, '-hide_banner', '-loglevel', 'error', '-i', path,
         '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
        stdout=subprocess.PIPE)
    frames = []
    while True:
        buf = p.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
            break
        frames.append(np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).copy())
    p.wait()
    assert len(frames) == n, f'解码 {len(frames)}/{n}'
    return frames


def extract_one(frame, d1, d2):
    try:
        return get_bwm(d1, d2).extract(embed_img=frame, wm_shape=744, mode='str') == WM_TEXT
    except Exception:
        return False


def run_case(pool, name, frames, d1=24, d2=14):
    oks = pool.starmap(extract_one, [(f, d1, d2) for f in frames], chunksize=4)
    print(f'  {name:<28}: {sum(oks)}/{len(oks)} = {sum(oks)/len(oks):.1%}', flush=True)


def main():
    from gpu_watermark import GpuWaterMarkCore
    from blind_watermark import WaterMark
    from blind_watermark.version import bw_notes
    bw_notes.close()
    bwm = WaterMark(password_img=1, password_wm=1)
    bwm.read_wm(WM_TEXT, mode='str')
    core36 = GpuWaterMarkCore(password_img=1, wm_bit=bwm.wm_bit.copy(), d1=36, d2=20)

    pool = Pool(min(12, os.cpu_count() or 8))
    for tag, start, n in SEGMENTS:
        print(f'== {tag} (帧{start}-{start + n - 1}) ==', flush=True)
        seg = read_segment(start, n)

        run_case(pool, 'A 1:1基线(直接解码)', seg)

        rec_encode(seg, W, H, 'rec_B_tmp.mp4', 18)
        run_case(pool, 'B1 全屏1:1录屏 crf18', rec_decode('rec_B_tmp.mp4', n, W, H))

        rec_encode(seg, W, H, 'rec_B_tmp.mp4', 23)
        run_case(pool, 'B2 全屏1:1录屏 crf23', rec_decode('rec_B_tmp.mp4', n, W, H))

        small = [cv2.resize(f, (1440, 810), interpolation=cv2.INTER_AREA) for f in seg]
        rec_encode(small, 1440, 810, 'rec_C_tmp.mp4', 23)
        frames_c = rec_decode('rec_C_tmp.mp4', n, 1440, 810)
        frames_c = [cv2.resize(f, (W, H), interpolation=cv2.INTER_LINEAR) for f in frames_c]
        run_case(pool, 'C 缩放75%录屏(还原提取)', frames_c)
        del small, frames_c

        d36 = []
        for i in range(0, n, 16):
            d36.append(core36.embed_bgr_batch(np.stack(seg[i:i + 16])))
        d36 = np.concatenate(d36)
        rec_encode(d36, W, H, 'rec_D_tmp.mp4', 23)
        run_case(pool, 'D 高强度d36版+crf23', rec_decode('rec_D_tmp.mp4', n, W, H), d1=36, d2=20)
        del d36

    pool.close()
    pool.join()
    for t in ('rec_B_tmp.mp4', 'rec_C_tmp.mp4', 'rec_D_tmp.mp4'):
        if os.path.exists(t):
            os.remove(t)
    print('完成', flush=True)


if __name__ == '__main__':
    main()
