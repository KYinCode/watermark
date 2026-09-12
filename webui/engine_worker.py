# -*- coding: utf-8 -*-
"""WebUI 提取常驻 worker(R6.3):stdin/stdout JSON 行协议,模型常驻保证提取秒级响应。

启动后先输出 {"type":"ready"};首次提取时加载模型并输出 {"type":"model_loaded"}。
请求:  {"id":..,"cmd":"extract_image"|"extract_video"|"extract_scan","path":..,"t":..,"window":..,"interval":..}
取消:  {"cmd":"cancel"}(无 id,协作式:帧间检查标志)
进度:  {"id":..,"type":"progress","done":..,"total":..,"note":..}
结果:  {"id":..,"type":"result", payload}
退出:  空闲超时(默认 600s)输出 {"type":"bye"} 后退出,释放显存。

payload 统一: status = hit|unknown|miss
  hit:    {id_hex, name, text, strategy, conf, votes, frames, strats, points, segments, ms}
  unknown:{id_hex, conf, ...同上}
  miss:   {strategy, conf, ...}
"""
import json
import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ / "tools"))
sys.path.insert(0, str(PROJ / "src"))

import extract_wm as E  # noqa: E402
import common as C      # noqa: E402
import wmlog            # noqa: E402

wmlog.setup_engine()  # 日志只走 stderr(后端已接进 worker.log);stdout 是 JSON 行协议通道
log = logging.getLogger("wm.worker")

IDLE_TIMEOUT = 600


def emit(obj: dict):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


class Worker:
    def __init__(self):
        self.wam = None
        self.dec = None
        self.known, self.works = E.load_codebook()
        self.cancel = threading.Event()

    def ensure_model(self):
        if self.dec is None:
            import contextlib
            log.info("开始加载提取模型…")
            t0 = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):  # WAM 库加载期打印走 stderr,不污染 JSON 行协议
                self.wam = E.load_wam()
            self.dec = E.Decoder(self.wam)
            log.info("提取模型加载完成,耗时 %.1fs", time.perf_counter() - t0)
            emit(dict(type="model_loaded"))

    def result(self, req_id, payload, t0):
        payload = dict(payload)
        payload["ms"] = int((time.perf_counter() - t0) * 1000)
        emit(dict(id=req_id, type="result", **payload))

    # ---------- 三种提取 ----------

    def extract_image(self, req_id, path, t0):
        import cv2
        self.ensure_model()
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return self.result(req_id, dict(status="error", message=f"无法读取图片文件: {path}"), t0)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        vid, strat, conf = E.strategy_chain(self.dec, img, set(self.known))
        base = dict(strategy=strat, conf=round(conf, 3), kind="image")
        if vid in self.known:
            w = self.known[vid]
            return self.result(req_id, dict(status="hit", id_hex=f"{vid:08x}",
                                            name=w["name"], text=w["text"], **base), t0)
        if vid is not None and conf >= 3.0:  # 阈值以下视为噪声,不判"未知 ID"
            return self.result(req_id, dict(status="unknown", id_hex=f"{vid:08x}", **base), t0)
        return self.result(req_id, dict(status="miss", **base), t0)

    def _frames_window(self, path, t, window, on_frame):
        """流式读邻域帧并逐帧回调;返回处理的帧数。取消时终止解码。"""
        fps, w, h = E.probe(path)
        start = max(t - window, 0)
        # -nostdin + stdin=DEVNULL: 防止 ffmpeg 继承常开的 stdin 管道后,交互监听线程阻塞导致
        # ffmpeg 写完也不退出、stdout 永不 EOF(实测会挂死整个提取)
        cmd = [C.FF, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", f"{start:.6f}", "-i", str(path),
               "-t", f"{max(2 * window, 1.0 / 60):.6f}", "-vsync", "0",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-vf", "scale=in_color_matrix=bt709", "-"]
        p = __import__("subprocess").Popen(cmd, stdout=__import__("subprocess").PIPE,
                                           stdin=__import__("subprocess").DEVNULL)
        n = 0
        try:
            while True:
                if self.cancel.is_set():
                    break
                buf = p.stdout.read(w * h * 3)
                if len(buf) < w * h * 3:
                    break
                on_frame(np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy())
                n += 1
        finally:
            try:
                p.kill()
            except Exception:
                pass
            p.wait()
        return n, fps

    def extract_video(self, req_id, path, t, window, t0):
        self.ensure_model()
        votes = {}
        strats = {}
        unknown_best = (None, 0.0)
        total_hint = int(2 * window * (E.probe(path)[0]))
        box = {"n": 0}

        def on_frame(f):
            vid, strat, conf = E.strategy_chain(self.dec, f, set(self.known))
            box["n"] += 1
            if vid in self.known:
                votes[vid] = votes.get(vid, 0) + 1
                strats[strat] = strats.get(strat, 0) + 1
            elif vid is not None and conf >= 3.0 and conf > unknown_best[1]:
                unknown_best = (vid, conf)
            emit(dict(id=req_id, type="progress", done=box["n"], total=max(total_hint, box["n"]),
                      note=f"帧 {box['n']}"))

        n, _ = self._frames_window(path, t, window, on_frame)
        base = dict(votes=votes, frames=n, strats=strats, window=window, t=t, kind="video")
        if votes:
            vid, cnt = max(votes.items(), key=lambda kv: kv[1])
            w = self.known[vid]
            return self.result(req_id, dict(status="hit", id_hex=f"{vid:08x}",
                                            name=w["name"], text=w["text"],
                                            hit_frames=cnt, **base), t0)
        if unknown_best[0] is not None:
            return self.result(req_id, dict(status="unknown", id_hex=f"{unknown_best[0]:08x}",
                                            conf=round(unknown_best[1], 3), **base), t0)
        return self.result(req_id, dict(status="miss", **base), t0)

    def extract_scan(self, req_id, path, interval, t0):
        self.ensure_model()
        fps, w, h = E.probe(path)
        duration = self._duration(path)
        ts = list(np.arange(0.0, max(duration - 0.2, 0), interval)) or [0.0]
        points = []
        votes_all = {}

        def one_point(t):
            frames = E.read_raw_window(path, t, t + 0.05, w, h)
            if not frames:
                points.append(dict(t=round(t, 2), status="miss"))
                return
            vid, strat, conf = E.strategy_chain(self.dec, frames[0], set(self.known))
            if vid in self.known:
                points.append(dict(t=round(t, 2), status="hit", id_hex=f"{vid:08x}", strategy=strat))
                votes_all[vid] = votes_all.get(vid, 0) + 1
            elif vid is not None and conf >= 3.0:
                points.append(dict(t=round(t, 2), status="unknown", id_hex=f"{vid:08x}",
                                   strategy=strat, conf=round(conf, 3)))
            else:
                points.append(dict(t=round(t, 2), status="miss", strategy=strat))

        for i, t in enumerate(ts):
            if self.cancel.is_set():
                break
            one_point(float(t))
            emit(dict(id=req_id, type="progress", done=i + 1, total=len(ts),
                      note=f"{t:.0f}s / {duration:.0f}s"))

        hit_ids = {p["id_hex"] for p in points if p.get("status") == "hit"}
        segments = []
        for hid in hit_ids:
            runs, cur = [], None
            for p in points:
                if p.get("status") == "hit" and p.get("id_hex") == hid:
                    if cur and abs(cur["end"] - p["t"]) < interval * 1.5:
                        cur["end"] = p["t"]
                    else:
                        cur = dict(start=p["t"], end=p["t"], id_hex=hid)
                        runs.append(cur)
                else:
                    cur = None
            segments.extend(runs)
        top = max(votes_all.items(), key=lambda kv: kv[1])[0] if votes_all else None
        payload = dict(kind="scan", interval=interval, duration=round(duration, 2),
                       points=points, segments=segments, frames=len(points),
                       votes=votes_all, strats={})
        if top is not None:
            w = self.known[top]
            payload.update(status="hit", id_hex=f"{top:08x}", name=w["name"], text=w["text"],
                           hit_frames=votes_all[top])
        elif any(p.get("status") == "unknown" for p in points):
            uid = next(p["id_hex"] for p in points if p.get("status") == "unknown")
            payload.update(status="unknown", id_hex=uid)
        else:
            payload.update(status="miss")
        return self.result(req_id, payload, t0)

    @staticmethod
    def _duration(path) -> float:
        import subprocess
        out = subprocess.run(
            [C.FFPROBE, "-v", "error", "-show_entries",
             "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True).stdout
        return float(json.loads(out)["format"]["duration"])


def main():
    worker = Worker()
    emit(dict(type="ready"))
    last_active = time.time()
    stop = threading.Event()

    def reader():
        """stdin 读取线程: 请求入队,取消命令置标志;EOF 置 eof 标志(由主循环排空后退出)"""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("cmd") == "cancel":
                log.info("收到取消命令,置取消标志")
                worker.cancel.set()
                continue
            if msg.get("cmd") == "exit":
                log.info("收到退出命令,排空队列后退出")
                eof.set()   # 排空队列后退出(硬终止由后端 kill 完成)
                return
            if "id" in msg:
                q.put(msg)
        eof.set()  # stdin 关闭(后端终止):主循环排空队列后自然退出

    import queue as _q
    q = _q.Queue()
    eof = threading.Event()
    threading.Thread(target=reader, daemon=True).start()

    handlers = {}
    while not stop.is_set():
        try:
            req = q.get(timeout=1.0)
        except _q.Empty:
            if eof.is_set() and q.empty():
                break
            if time.time() - last_active > IDLE_TIMEOUT:
                log.info("空闲超过 %ds,退出释放显存", IDLE_TIMEOUT)
                break
            continue
        last_active = time.time()
        worker.cancel.clear()
        t0 = time.perf_counter()
        log.info("处理请求 %s cmd=%s", req.get("id"), req.get("cmd"))
        try:
            # 码本每次请求时重读(支持 WebUI 增删作品后即时生效)
            worker.known, worker.works = E.load_codebook()
            cmd = req["cmd"]
            if cmd == "extract_image":
                worker.extract_image(req["id"], req["path"], t0)
            elif cmd == "extract_video":
                worker.extract_video(req["id"], req["path"], float(req.get("t", 0)),
                                     float(req.get("window", 0.5)), t0)
            elif cmd == "extract_scan":
                worker.extract_scan(req["id"], req["path"], float(req.get("interval", 5)), t0)
            elif cmd == "warm":          # 预热:加载模型后返回(后端启动/嵌入完成后调用)
                worker.ensure_model()
                emit(dict(id=req["id"], type="result", status="pong"))
            elif cmd == "ping":
                emit(dict(id=req["id"], type="result", status="pong"))
            else:
                emit(dict(id=req["id"], type="result", status="error",
                          message=f"未知命令 {cmd}"))
        except Exception as e:
            log.exception("请求 %s 处理异常", req.get("id"))
            emit(dict(id=req["id"], type="result", status="error",
                      message=f"{type(e).__name__}: {e}"))
        log.info("请求 %s 处理返回,耗时 %dms", req.get("id"),
                 int((time.perf_counter() - t0) * 1000))
        last_active = time.time()

    emit(dict(type="bye"))
    log.info("worker 退出")


if __name__ == "__main__":
    main()
