# -*- coding: utf-8 -*-
"""wm2 公共库:WAM 模型加载、ffmpeg 流水线 IO、消息码本、解码统计。

可移植性约定(2026-09-06):所有外部工具路径都不写死——
ffmpeg/ffprobe 解析顺序:环境变量 WM_FFMPEG → 项目 bin\ → PATH → 常见安装位置;
pip/HF 等可下载资源一律装/缓存在项目目录内(runtime\、hf_home\)。
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

PROJ = Path(__file__).resolve().parent.parent
REPO = PROJ / "third_party" / "watermark-anything"
os.environ.setdefault("HF_HOME", str(PROJ / "hf_home"))  # HF 缓存留在项目内,搬家不丢


def _resolve_tool(name: str) -> str:
    env = os.environ.get("WM_FFMPEG")
    if env:
        p = Path(env)
        cand = p / name if p.is_dir() else p
        if cand.exists():
            return str(cand)
    cand = PROJ / "bin" / name
    if cand.exists():
        return str(cand)
    import shutil
    which = shutil.which(name)
    if which:
        return which
    for base in (r"C:\Environment\FFmpeg\FFmpeg_Builds\bin",  # 本机既有安装
                 r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"):
        cand = Path(base) / name
        if cand.exists():
            return str(cand)
    return name  # 兜底:交给报错;入口(启动WebUI --check / 安装环境.bat)有体检与引导


FF = _resolve_tool("ffmpeg.exe")
FFPROBE = _resolve_tool("ffprobe.exe")

DATA = PROJ / "data"                              # 输入投放区(扁平,文件直接放这里)
OUT = PROJ / "experiments" / "out"                # 实验中间产物
OUTPUT = PROJ / "output"                          # 成品输出目录
CODEBOOK = PROJ / "tools" / "codebook.json"
VERIFY_DIR = OUTPUT / "verification"              # 验收原始 JSON
W, H, FPS = 1920, 1080, 60                        # 验收攻击几何的基准分辨率

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm"}
IMG_EXTS = {".png", ".jpg", ".jpeg"}


def data_videos():
    return sorted(p for p in DATA.iterdir() if p.suffix.lower() in VIDEO_EXTS)


def data_images():
    return sorted(p for p in DATA.iterdir() if p.suffix.lower() in IMG_EXTS)


def one_video():
    """data\\ 下应恰好有 1 个视频;多素材时改用 --input 显式指定"""
    vs = data_videos()
    assert len(vs) == 1, f"data\\ 下应有且只有 1 个视频,当前 {len(vs)} 个:{vs}(多素材时用 --input 指定)"
    return vs[0]


def probe(path):
    """ffprobe -> (fps, w, h, 总帧数或 None)"""
    import json as _json
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=avg_frame_rate,width,height,nb_frames",
         "-of", "json", str(path)],
        capture_output=True, text=True).stdout
    st = _json.loads(out)["streams"][0]
    num, den = st["avg_frame_rate"].split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    nb = int(st.get("nb_frames") or 0)
    return fps, int(st["width"]), int(st["height"]), (nb or None)

WM_TEXT = '©Yin(Yin_Code/KYin_Code/KYin)版权所有|禁止搬运 Bili:3706940936948128 Douyin:Yin_Code'
NBITS = 32


def derive_id(text: str) -> int:
    """作品文本 -> 32bit ID(sha256(text utf-8) 前 4 字节,大端)。派生规则不可变。"""
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big")


def text_to_bits(text: str) -> np.ndarray:
    """文本 -> 固定 32bit ID 的比特向量(derive_id 的展开形式)"""
    val = derive_id(text)
    return np.array([(val >> (NBITS - 1 - i)) & 1 for i in range(NBITS)], dtype=np.float32)


def wm_msg_bits() -> np.ndarray:
    """默认作品文本 -> 32bit(向后兼容入口)"""
    return text_to_bits(WM_TEXT)


def write_codebook(path: Path):
    val = 0
    for b in wm_msg_bits():
        val = (val << 1) | int(b)
    path.write_text(json.dumps({
        "scheme": "WAM wam_mit 32-bit fixed ID; ID=sha256(text)[:4] big-endian",
        "wm_text": WM_TEXT,
        "id_hex": f"{val:08x}",
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load_wam(scaling_w: float = 2.0, ckpt_name: str = "wam_mit.pth"):
    import argparse
    import omegaconf
    import torch
    sys.path.insert(0, str(REPO))
    from watermark_anything.models import Wam, build_embedder, build_extractor
    from watermark_anything.augmentation.augmenter import Augmenter
    from watermark_anything.modules.jnd import JND
    from watermark_anything.data.transforms import unnormalize_img, normalize_img

    params = json.load(open(REPO / "checkpoints" / "params.json"))
    args = argparse.Namespace(**params)
    ecfg = omegaconf.OmegaConf.load(REPO / args.embedder_config)
    xcfg = omegaconf.OmegaConf.load(REPO / args.extractor_config)
    acfg = omegaconf.OmegaConf.load(REPO / args.augmentation_config)
    tcfg = omegaconf.OmegaConf.load(REPO / args.attenuation_config)
    embedder = build_embedder(args.embedder_model, ecfg[args.embedder_model], args.nbits)
    extractor = build_extractor(xcfg.model, xcfg[args.extractor_model], args.img_size, args.nbits)
    augmenter = Augmenter(**acfg)
    try:
        attenuation = JND(**tcfg[args.attenuation], preprocess=unnormalize_img, postprocess=normalize_img)
    except Exception:
        attenuation = None
    wam = Wam(embedder, extractor, augmenter, attenuation, args.scaling_w, args.scaling_i)
    sd = torch.load(REPO / "checkpoints" / ckpt_name, map_location="cpu")
    wam.load_state_dict(sd)
    wam.scaling_w = scaling_w
    wam = wam.eval().cuda()
    # 显存护栏: 锁 45% (~3.7GiB),超限先清缓存,避免 WDDM 倒腾共享内存卡死桌面
    torch.cuda.set_per_process_memory_fraction(0.45, 0)
    return wam


def norm_frames(batch_rgb: np.ndarray):
    """uint8 RGB BHWC -> ImageNet 归一化 BCHW float cuda"""
    import torch
    x = torch.from_numpy(batch_rgb).cuda().permute(0, 3, 1, 2).float() / 255.0
    mean = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)
    return (x - mean) / std


def unnorm_to_uint8(t):
    import torch
    mean = torch.tensor([0.485, 0.456, 0.406], device=t.device).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=t.device).view(1, 3, 1, 1)
    y = (t * std + mean).clamp(0, 1)
    return (y * 255.0).round().byte().permute(0, 2, 3, 1).cpu().numpy()


def read_frames(path, start_frame=0, n=None, w=W, h=H):
    """ffmpeg 解码 -> uint8 RGB (n,h,w,3)。start_frame 用输出寻址,帧号精确。"""
    cmd = [FF, "-hide_banner", "-loglevel", "error"]
    if start_frame > 0:
        cmd += ["-ss", f"{(start_frame - 0.5) / 60.0:.6f}"]
    cmd += ["-i", str(path)]
    if n is not None:
        cmd += ["-frames:v", str(n)]
    cmd += ["-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-vf", "scale=in_color_matrix=bt709", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    frames = []
    while True:
        buf = p.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
            break
        frames.append(np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).copy())
    p.wait()
    return frames


def encode_frames(frames, path, crf, w=W, h=H, fps=FPS, preset="medium"):
    p = subprocess.Popen(
        [FF, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
         "-vf", "scale=out_color_matrix=bt709,format=yuv420p",
         "-c:v", "libx264", "-preset", preset, "-crf", str(crf), str(path)],
        stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    assert p.wait() == 0, f"编码失败 {path}"


def decode_batch_stats(wam, frames_iter, n, msg, batch=8, report_ms=True):
    """流式逐帧 detect,返回 (bit_acc[], coverage[], ms/frame)。frames_iter: uint8 RGB 生成器或列表"""
    import torch
    accs, covs = [], []
    t0 = None
    buf = []
    with torch.no_grad():
        for f in frames_iter:
            buf.append(f)
            if len(buf) < batch:
                continue
            x = norm_frames(np.stack(buf))
            buf = []
            if t0 is None and report_ms:
                torch.cuda.synchronize(); t0 = time_start()
            accs_b, covs_b = _detect_one(wam, x, msg)
            accs += accs_b; covs += covs_b
        if buf:
            if t0 is None and report_ms:
                torch.cuda.synchronize(); t0 = time_start()
            x = norm_frames(np.stack(buf))
            accs_b, covs_b = _detect_one(wam, x, msg)
            accs += accs_b; covs += covs_b
    if report_ms:
        torch.cuda.synchronize()
        ms = time_ms(t0, n)
    else:
        ms = -1
    return np.array(accs), np.array(covs), ms


def _detect_one(wam, x, msg):
    import torch
    preds = wam.detect(x)["preds"]
    mask = torch.sigmoid(preds[:, 0])          # b h w
    bits = preds[:, 1:]                         # b k h w
    accs, covs = [], []
    for b in range(mask.shape[0]):
        sel = mask[b] > 0.5
        cov = sel.float().mean().item()
        covs.append(cov)
        if sel.sum() < 8:
            accs.append(0.0)
            continue
        bm = bits[b][:, sel].mean(dim=1)        # k
        pred = (bm > 0).float().cpu().numpy()
        accs.append(float((pred == msg).mean()))
    return accs, covs


def read_frames_iter(path, w, h):
    """解码文件 -> uint8 RGB 生成器(流式)"""
    p = subprocess.Popen(
        [FF, "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-vf", "scale=in_color_matrix=bt709", "-"],
        stdout=subprocess.PIPE)
    try:
        while True:
            buf = p.stdout.read(w * h * 3)
            if len(buf) < w * h * 3:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).copy()
    finally:
        p.stdout.close()
        p.wait()


def probe_video_size(path):
    import json as _json
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(path)],
        capture_output=True, text=True).stdout
    st = _json.loads(out)["streams"][0]
    return int(st["width"]), int(st["height"])


def read_frames_iter_auto(path):
    w, h = probe_video_size(path)
    return read_frames_iter(path, w, h)


_t0 = None
def time_start():
    import time
    return time.perf_counter()

def time_ms(t0, n):
    import time
    return (time.perf_counter() - t0) * 1000.0 / max(n, 1)


def psnr_db(a: np.ndarray, b: np.ndarray) -> float:
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def imread_unicode(path) -> np.ndarray:
    import cv2
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)

def imwrite_unicode(path, img_bgr) -> bool:
    import cv2
    ext = str(path).rsplit(".", 1)[-1]
    ok, buf = cv2.imencode("." + ext, img_bgr)
    assert ok
    buf.tofile(str(path))
    return True
