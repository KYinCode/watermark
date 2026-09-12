# -*- coding: utf-8 -*-
"""wm2 水印提取工具(WAM 版,两步式增强,多 ID 查表)。

单帧图片:  python tools/extract_wm.py image <图片路径>
视频时间点: python tools/extract_wm.py video <视频路径> (--t 秒 | --frame 帧号) [--window 0.5]

码本(tools/codebook.json)支持两种格式:
  v1(旧): {"scheme","wm_text","id_hex"} —— 单作品
  v2(新): {"version":2,"scheme","works":[{"id_hex","name","text","note","created_at"}]} —— 多作品
命中判定: 解出 32bit ID 且 ID 在码本登记;已解出但未登记的 ID 输出"未知 ID"。

策略链(依次尝试,码本命中即返回):
  1. 全帧直解
  2. 全帧镜像解(镜像+裁剪组合盲区)
  3. 定位 mask bbox 裁剪解(包边/贴片后剩余区域)
  4. 3x3 多窗搜索解(50% 面积窗,包边缩放场景)
  5. 角度搜索解(±10° 步长1°, 45°~75°/负角 步长2°,旋转取证)
视频模式: 先取 ±window 秒邻域逐帧执行策略链,再对多帧结果做多数投票。
"""
import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "src"))
import common as C  # noqa: E402  (FF/FFPROBE 按 common 的可移植性约定解析:WM_FFMPEG → bin\ → PATH → 常见位置)
import wmlog  # noqa: E402


def load_codebook():
    """读取码本 -> (known_ids: {id_int: work}, works: [work])。work=dict(id_hex,name,text)。兼容 v1/v2。"""
    cb = json.loads((PROJ / "tools" / "codebook.json").read_text(encoding="utf-8"))
    if "works" in cb:
        works = []
        for w in cb["works"]:
            works.append(dict(id_hex=w["id_hex"], name=w.get("name", ""),
                              text=w["text"], note=w.get("note", "")))
    else:  # v1 单作品
        works = [dict(id_hex=cb["id_hex"], name="默认作品", text=cb["wm_text"], note="")]
    known = {int(w["id_hex"], 16): w for w in works}
    return known, works


def load_wam():
    return C.load_wam(scaling_w=2.0)


class Decoder:
    def __init__(self, wam, nbits=32):
        import torch
        self.wam = wam
        self.torch = torch
        self.nbits = nbits

    def soft_bits(self, frame):
        """单帧 -> (soft bits [32] 或 None, coverage)"""
        x = self._norm(frame[None])
        with self.torch.no_grad():
            preds = self.wam.detect(x)["preds"]
        mask = self.torch.sigmoid(preds[0, 0])
        bits = preds[0, 1:]
        sel = mask > 0.5
        cov = float(sel.float().mean())
        if sel.sum() < 8:
            return None, cov
        return bits[:, sel].mean(dim=1).cpu().numpy().astype(np.float32), cov

    def _norm(self, batch):
        return C.norm_frames(batch)

    def bbox(self, frame, thresh=0.5, shrink=0.0):
        x = self._norm(frame[None])
        with self.torch.no_grad():
            preds = self.wam.detect(x)["preds"]
        mask = self.torch.sigmoid(preds[0, 0]).cpu().numpy()
        sel = (mask > thresh).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(sel, 8)
        if n <= 1:
            return None
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x0, y0 = stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_TOP]
        w0, h0 = stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT]
        if w0 < 24 or h0 < 24:
            return None
        fh, fw = frame.shape[:2]
        mx, my = int(w0 * shrink), int(h0 * shrink)
        gx0, gy0 = int((x0 + mx) / 256 * fw), int((y0 + my) / 256 * fh)
        gx1, gy1 = int((x0 + w0 - mx) / 256 * fw), int((y0 + h0 - my) / 256 * fh)
        if gx1 - gx0 < 48 or gy1 - gy0 < 48:
            return None
        return gx0, gy0, gx1, gy1


def inv_rot(frame, angle):
    h, w = frame.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    r = cv2.warpAffine(frame, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    ch, cw = min(1080, h), min(1920, w)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return r[y0:y0 + ch, x0:x0 + cw]


def iter_trials(dec, frame):
    """按需产出 (策略名, 变换后帧)。贵的变换(HSV 网格/35 组旋转)只在前序全未命中时才计算,
    典型命中('full' 首中)耗时从 ~0.5s 降到单次 detect(~30-60ms)。顺序与旧版完全一致。"""
    yield "full", frame
    yield "mirror", np.ascontiguousarray(frame[:, ::-1])
    # 亮度/色相补偿网格(实测: 亮度-20% 好段 75.2%→网格 81.1%; 色相-72° hd 段 2.7%→网格 100%)
    for d in (13, 26, 39, 51, -13, -26, -39, -51):
        yield (f"bright{d:+d}", np.clip(frame.astype(np.int16) + d, 0, 255).astype(np.uint8))
    base_hsv = None
    for s in (12, 24, 36, 48, 60, 72, -12, -24, -36, -48, -60, -72):
        if base_hsv is None:
            base_hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV).astype(np.int16)
        hsv = base_hsv.copy()
        hsv[:, :, 0] = (hsv[:, :, 0] + s) % 180
        yield (f"hue{s:+d}", cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB))
    bb = dec.bbox(frame)
    if bb is not None:
        x0, y0, x1, y1 = bb
        yield ("maskbbox", frame[y0:y1, x0:x1])
    h, w = frame.shape[:2]
    for wy in range(3):
        for wx in range(3):
            x0 = int(wx * w / 4); y0 = int(wy * h / 4)
            x1 = min(x0 + w // 2, w); y1 = min(y0 + h // 2, h)
            if x1 - x0 >= 48 and y1 - y0 >= 48:
                yield (f"win{wy}{wx}", frame[y0:y1, x0:x1])
    for ang in list(range(-10, 11)) + list(range(44, 77, 2)) + list(range(-74, -43, 2)):
        yield (f"rot{ang}", inv_rot(frame, ang))


def strategy_chain(dec, frame, known_ids):
    """两步式策略链。返回 (命中ID或最佳候选ID, 策略名, 置信度)。
    解出 ID ∈ known_ids -> 命中即返回;否则返回全程置信度最高的候选(可能为未登记 ID 或 None)。"""
    best = (None, "none", 0.0)
    for name, fr in iter_trials(dec, frame):
        soft, cov = dec.soft_bits(fr)
        if soft is None:
            continue
        vid = bits2id(soft)
        conf = float(np.abs(soft - 0.5).mean())  # 位越接近0/1越自信
        if vid in known_ids:
            return vid, name, conf
        if conf > best[2]:
            best = (vid, name, conf)
    return best


def bits2id(bits):
    v = 0
    for b in bits:
        v = (v << 1) | int(b > 0.5)
    return v


def read_raw_window(path, start, end, w, h):
    # -nostdin + stdin=DEVNULL: 常驻服务场景下继承的 stdin 管道会让 ffmpeg 退出阻塞(见 engine_worker)
    cmd = [C.FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start:.6f}", "-i", str(path),
           "-t", f"{max(end - start, 1.0 / 60):.6f}", "-vsync", "0",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-vf", "scale=in_color_matrix=bt709", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    frames = []
    while True:
        buf = p.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
            break
        frames.append(np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy())
    p.wait()
    return frames


def probe(path):
    out = subprocess.run([C.FFPROBE, "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=avg_frame_rate,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    st = json.loads(out)["streams"][0]
    num, den = st["avg_frame_rate"].split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    return fps, int(st["width"]), int(st["height"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["image", "video"])
    ap.add_argument("path")
    ap.add_argument("--t", type=float, default=None)
    ap.add_argument("--frame", type=int, default=None)
    ap.add_argument("--window", type=float, default=0.5)
    ap.add_argument("--log-file", type=str, default=None,
                    help="日志文件(可选):提取流水带时间戳写入该文件,控制台输出不变")
    args = ap.parse_args()
    if args.log_file:
        wmlog.setup_cli(args.log_file)
    log = logging.getLogger("wm.cli.extract_wm")

    known, works = load_codebook()
    wam = load_wam()
    dec = Decoder(wam)

    if args.mode == "image":
        img = cv2.cvtColor(cv2.imdecode(np.fromfile(args.path, dtype=np.uint8), cv2.IMREAD_COLOR),
                           cv2.COLOR_BGR2RGB)
        vid, strat, conf = strategy_chain(dec, img, set(known))
        if vid in known:
            w = known[vid]
            print(f"[命中] 策略={strat}  ID={vid:08x}")
            print(f"作品: {w['name']}" if w["name"] else "[命中]")
            print("版权文本:", w["text"])
            log.info("命中 策略=%s ID=%08x 作品=%s", strat, vid, w["name"])
            sys.exit(0)
        shown = f"{vid:08x}" if vid is not None else "----"
        if vid is not None and conf >= 3.0:
            print(f"[未知 ID] ID={shown} 策略={strat} 置信度={conf:.3f}")
            print("(水印已解出但该 ID 未在码本登记;可能码本条目已被删除)")
            log.info("未知 ID=%s 策略=%s 置信度=%.3f", shown, strat, conf)
        else:
            print(f"[未检出] 最佳候选 ID={shown} 策略={strat} 置信度={conf:.3f}")
            print("(非本项目水印,或画面不含足够水印区域)")
            log.info("未检出 最佳候选 ID=%s 策略=%s 置信度=%.3f", shown, strat, conf)
        sys.exit(3)

    fps, w, h = probe(args.path)
    if args.frame is not None:
        t = args.frame / fps
    elif args.t is not None:
        t = args.t
    else:
        print("需要 --t 或 --frame"); sys.exit(1)
    frames = read_raw_window(args.path, max(t - args.window, 0), t + args.window, w, h)
    if not frames:
        print("邻域内无帧"); sys.exit(2)
    votes = {}          # 命中码本的 ID -> 帧数
    strats = {}
    unknown_best = (None, 0.0)   # (未登记 ID, 最高置信度)  阈值 0.30 以下视为噪声
    for f in frames:
        vid, strat, conf = strategy_chain(dec, f, set(known))
        if vid in known:
            votes[vid] = votes.get(vid, 0) + 1
            strats[strat] = strats.get(strat, 0) + 1
        elif vid is not None and conf >= 3.0 and conf > unknown_best[1]:
            unknown_best = (vid, conf)
    if votes:
        vid, n_hit = max(votes.items(), key=lambda kv: kv[1])
        w = known[vid]
        print(f"邻域帧数: {len(frames)}  码本命中帧: {n_hit}  命中策略分布: {strats}")
        print(f"[命中] ID={vid:08x}")
        print(f"作品: {w['name']}" if w["name"] else "[命中]")
        print("版权文本:", w["text"])
        log.info("命中 ID=%08x 命中帧=%d/%d 策略分布=%s", vid, n_hit, len(frames), strats)
        sys.exit(0)
    shown = f"{unknown_best[0]:08x}" if unknown_best[0] is not None else "----"
    if unknown_best[0] is not None:
        print(f"[未知 ID] 邻域内解出未登记 ID={shown} (置信度={unknown_best[1]:.3f}),码本无此条目")
        log.info("未知 ID=%s 置信度=%.3f", shown, unknown_best[1])
    else:
        print("[未命中] 邻域内所有帧均未解出本项目水印")
        log.info("未命中 邻域帧数=%d", len(frames))
    sys.exit(3)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("wm.cli.extract_wm").exception("运行失败")
        raise SystemExit(1)
