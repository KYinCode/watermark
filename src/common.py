# -*- coding: utf-8 -*-
"""wm2 公共库:WAM 模型加载、ffmpeg 流水线 IO、消息码本、解码统计。

可移植性约定(2026-09-06):所有外部工具路径都不写死——
ffmpeg/ffprobe 解析顺序:环境变量 WM_FFMPEG → 项目 bin 目录 → PATH → 常见安装位置;
pip/HF 等可下载资源一律装/缓存在项目目录内(runtime\\、hf_home\\)。
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
os.environ.setdefault("TORCH_HOME", str(PROJ / "torch_home"))  # torchvision/lpips 权重同理


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
    except Exception as e:
        print(f"[warn] JND 配置加载失败({e});水印退化为无 JND 掩码的均匀嵌入,画质与鲁棒性将改变", flush=True)
        attenuation = None
    wam = Wam(embedder, extractor, augmenter, attenuation, args.scaling_w, args.scaling_i)
    sd = torch.load(REPO / "checkpoints" / ckpt_name, map_location="cpu", weights_only=True)
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


def read_frames(path, start_frame=0, n=None, w=W, h=H, fps=FPS):
    """ffmpeg 解码 -> uint8 RGB (n,h,w,3)。start_frame 用输出寻址,帧号精确。"""
    cmd = [FF, "-hide_banner", "-loglevel", "error"]
    if start_frame > 0:
        cmd += ["-ss", f"{(start_frame - 0.5) / fps:.6f}"]
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


PART_PREFIX = ".part-"


def out_name(stem: str, ext: str) -> Path:
    """<源名>_已加水印<ext>,重名自动 _v2.._v99(引擎/CLI/图片共用)"""
    cand = OUTPUT / f"{stem}_已加水印{ext}"
    k = 2
    while cand.exists():
        cand = OUTPUT / f"{stem}_已加水印_v{k}{ext}"
        k += 1
        if k > 99:
            raise RuntimeError("成品重名过多,请清理 output\\")
    return cand


def load_payload(text: str, scaling_w: float):
    """版权文本 -> (wam, msg1, expect_bits, expect_id):引擎/CLI 共用的加载入口(规则不可变)"""
    import torch
    expect_id = derive_id(text)
    expect_bits = text_to_bits(text)
    wam = load_wam(scaling_w=scaling_w)
    msg1 = torch.from_numpy(expect_bits).float().unsqueeze(0).cuda()
    return wam, msg1, expect_bits, expect_id


def selfcheck_frame(wam, product_path, expect_bits, frame_no, w, h, fps):
    """对成品抽 1 帧直解自检(R2.6)"""
    frames = read_frames(product_path, frame_no, 1, w=w, h=h, fps=fps)
    if not frames:
        return dict(hit=False, acc=0.0, note="抽帧失败")
    accs, _, _ = decode_batch_stats(wam, frames, 1, expect_bits, report_ms=False)
    return dict(hit=bool(accs[0] == 1.0), acc=round(float(accs[0]), 4))


def _read_exact(stream, nbytes):
    """读满 nbytes;EOF 时返回已有部分(可能不足),完全无数据返回 None。
    返回 bytearray(可写):np.frombuffer 包出的数组因此可写,torch.from_numpy
    共享其内存时不会触发 NumPy non-writable UserWarning,且无额外复制。"""
    buf = bytearray()
    while len(buf) < nbytes:
        chunk = stream.read(nbytes - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return buf if buf else None


def embed_video(wam, msg1, expect_bits, src: Path, crf: int, part: Path,
                comp: float = 0.0, emit=None):
    """全片嵌入核心(引擎/CLI 唯一实现,以引擎版逻辑为基准):
    流式 ffmpeg 解码 -> 逐帧 WAM 嵌入(可选色彩回补)-> 编码到 part -> 帧数校验 -> 中段抽帧自检。
    emit 可选: 回调接收事件 dict(type=start/progress/psnr/selfcheck);None 则静默。
    返回 dict(frames, psnr{mean,min,p5}, wall_s, w, h, fps, embed_ms)。"""
    import time
    import torch
    emit = emit or (lambda ev: None)
    fps, w, h, total = probe(src)
    emit(dict(type="start", kind="video", input=src.name, total=total, w=w, h=h, fps=round(fps, 3)))
    mean_t = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
    std_t = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)

    dec = subprocess.Popen(
        [FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src),
         "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-vf", "scale=in_color_matrix=bt709", "-"],
        stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    enc = subprocess.Popen(
        [FF, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "-",
         "-i", str(src),
         "-map", "0:v", "-map", "1:a?",
         "-vf", "scale=out_color_matrix=bt709,format=yuv420p,"
                "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-c:a", "copy", str(part)],
        stdin=subprocess.PIPE)

    B = 8
    frame_bytes = w * h * 3
    psnr_samples = []
    n_done = 0
    t0 = time.perf_counter()
    t_embed = 0.0
    try:
        while True:
            raw = _read_exact(dec.stdout, frame_bytes * B)
            if raw is None:
                break
            batch = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)
            tg = time.perf_counter()
            if comp:  # 色彩回补:嵌入前红绿各预减 N 级(等效蓝差回补;白底 B=255 顶格不能直接加蓝)。PSNR 对未补偿源
                f = batch.astype(np.float32)
                f[:, :, :, 0] = np.clip(f[:, :, :, 0] - comp, 0, 255)
                f[:, :, :, 1] = np.clip(f[:, :, :, 1] - comp, 0, 255)
                x = norm_frames(f)
                base = torch.from_numpy(batch).cuda().permute(0, 3, 1, 2).float() / 255.0
            else:
                x = norm_frames(batch)
                base = x * std_t + mean_t
            with torch.no_grad():
                out = wam.embed(x, msg1.repeat(x.shape[0], 1))
            y8 = (out["imgs_w"] * std_t + mean_t).clamp(0, 1).mul(255).round().byte()
            yf = y8.float() / 255.0
            mse = ((yf - base) ** 2).mean(dim=(1, 2, 3))
            psnr_samples.append((-10 * torch.log10(mse + 1e-12)).cpu().numpy())
            wm = y8.permute(0, 2, 3, 1).cpu().numpy()
            t_embed += time.perf_counter() - tg
            enc.stdin.write(wm.tobytes())
            n_done += len(batch)
            emit(dict(type="progress", done=n_done, total=total or n_done))
            last = len(batch) < B
            del x, base, out, y8, yf, mse, wm, batch
            if last:
                break
    finally:
        try:
            enc.stdin.close()
        except Exception:
            pass
        try:
            dec.stdout.close()
        except Exception:
            pass
        rc_dec = dec.wait()
        rc_enc = enc.wait()
    if rc_enc != 0:
        raise RuntimeError(f"编码失败 rc={rc_enc} rc_dec={rc_dec}")

    wall = time.perf_counter() - t0
    psnrs = np.concatenate(psnr_samples)
    psnr_stat = dict(mean=round(float(psnrs.mean()), 2), min=round(float(psnrs.min()), 2),
                     p5=round(float(np.percentile(psnrs, 5)), 2))
    emit(dict(type="psnr", **psnr_stat))

    # 帧数校验
    pr = subprocess.run(
        [FFPROBE, "-v", "error", "-count_frames",
         "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "json",
         str(part)], capture_output=True, text=True).stdout
    nb = int(json.loads(pr)["streams"][0]["nb_read_frames"])
    if nb != n_done:
        raise RuntimeError(f"帧数校验失败: 成品 {nb} 帧 != 嵌入 {n_done} 帧")

    # 自检: 中段抽 1 帧直解(R2.6)
    sc = selfcheck_frame(wam, part, expect_bits, n_done // 2, w, h, fps)
    emit(dict(type="selfcheck", index=0, **sc))
    return dict(frames=n_done, psnr=psnr_stat, wall_s=round(wall, 1), w=w, h=h, fps=fps,
                embed_ms=round(1000 * t_embed / max(n_done, 1), 2))


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
