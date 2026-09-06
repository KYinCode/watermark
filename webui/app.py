# -*- coding: utf-8 -*-
"""watermark-v2 WebUI 后端(FastAPI,仅绑定 127.0.0.1,单人本机自用)。

结构:
  - 码本管理  -> webui/store.py(tools/codebook.json v2,CLI 与 WebUI 同一份存储,R1.5)
  - 引擎子进程 -> webui/engine_embed.py(嵌入,JSON 行进度)、webui/engine_worker.py(提取常驻)
  - GPU 串行   -> 单队列线程:任何时刻至多 1 个 GPU 进程;嵌入启动前先终止提取 worker 释放显存
  - 可靠性     -> 任务/进度持久化 webui/data/jobs.json;重启后 running->interrupted,可重新排队;
                  启动时清理 output/.part-* 半成品
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ / "src"))
import common as C  # noqa: E402

from store import CodebookError, add_work, delete_work, edit_work, load as cb_load, works as cb_works  # noqa: E402

WEBUI = PROJ / "webui"
STATIC = WEBUI / "static"
DATA_DIR = WEBUI / "data"
LOGS = DATA_DIR / "logs"
JOBS_JSON = DATA_DIR / "jobs.json"
for d in (DATA_DIR, LOGS, C.OUTPUT, C.DATA):
    d.mkdir(parents=True, exist_ok=True)

MEDIA_EXTS = C.VIDEO_EXTS | C.IMG_EXTS
PORT = 8765

app = FastAPI(title="wm2 水印工作台")

# ---------------------------------------------------------------- 工具


def iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "文件名为空")
    name = Path(name.replace("\\", "/")).name  # 去目录成分
    return name


def check_media_path(p: Path, must_exist=True):
    if must_exist and not p.exists():
        raise HTTPException(404, f"文件不存在: {p}")
    if p.suffix.lower() not in MEDIA_EXTS:
        raise HTTPException(400, f"不支持的格式 {p.suffix or '(无后缀)'};支持 "
                                 f"{', '.join(sorted(MEDIA_EXTS))}")


def under_proj(p: Path) -> bool:
    try:
        rp = p.resolve()
        return str(rp).startswith(str(PROJ.resolve()))
    except OSError:
        return False


def ffprobe_full(path: Path) -> dict:
    """视频/图片规格探测,失败给出可读错误(R2.8)"""
    ffprobe = C.FFPROBE
    r = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,avg_frame_rate,width,height,nb_frames",
         "-show_entries", "format=duration,size", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise HTTPException(400, f"无法读取该文件(ffprobe 失败):{r.stderr.strip()[:200]}")
    try:
        info = json.loads(r.stdout)
        v = next(s for s in info["streams"] if s.get("codec_type") == "video" or
                 s.get("codec_name") not in (None, "aac", "mp3"))
        has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
        num, den = (v.get("avg_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den) if float(den or 0) else 0.0
        return dict(codec=v.get("codec_name"), w=int(v.get("width") or 0), h=int(v.get("height") or 0),
                    fps=round(fps, 3), frames=int(v.get("nb_frames") or 0),
                    duration=round(float(info["format"].get("duration") or 0), 2),
                    audio=has_audio)
    except Exception as e:
        raise HTTPException(400, f"无法解析该文件:{e}")


# ---------------------------------------------------------------- 任务存储


class JobStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: list = []
        self._last_save = 0.0
        self._load()

    def _load(self):
        if JOBS_JSON.exists():
            try:
                self.jobs = json.loads(JOBS_JSON.read_text(encoding="utf-8"))["jobs"]
            except Exception:
                self.jobs = []
        # 重启恢复:running -> interrupted(允许重新排队);清理半成品
        changed = False
        for j in self.jobs:
            if j["status"] == "running":
                j["status"] = "interrupted"
                j["error"] = j.get("error") or "后端重启,任务中断"
                changed = True
        for part in C.OUTPUT.glob(".part-*"):
            try:
                part.unlink()
                changed = True
            except OSError:
                pass
        if changed or not JOBS_JSON.exists():
            self.save(force=True)

    def save(self, force=False):
        now = time.time()
        if not force and now - self._last_save < 1.0:
            return
        self._last_save = now
        with self.lock:
            if len(self.jobs) > 500:
                keep = [j for j in self.jobs if j["status"] in ("running", "queued")]
                done = [j for j in self.jobs if j["status"] not in ("running", "queued")]
                self.jobs = keep + done[-(500 - len(keep)):]
            tmp = JOBS_JSON.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"jobs": self.jobs}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(JOBS_JSON)

    def create(self, kind, label, params) -> dict:
        job = dict(id=f"{int(time.time()*1000):x}-{uuid.uuid4().hex[:4]}", kind=kind, label=label,
                   status="queued", params=params, progress=dict(done=0, total=0, phase="排队中",
                                                                 pct=0, eta_s=None),
                   result={}, error="", part=None,
                   created_at=iso(), started_at=None, finished_at=None)
        with self.lock:
            self.jobs.append(job)
        self.save(force=True)
        return job

    def get(self, job_id) -> dict:
        for j in self.jobs:
            if j["id"] == job_id:
                return j
        raise HTTPException(404, "任务不存在")

    def snapshot(self):
        with self.lock:
            return [dict(j) for j in self.jobs]


JOBS = JobStore()

# ---------------------------------------------------------------- 提取常驻 worker


class EngineWorker:
    """engine_worker.py 子进程管理:stdin/stdout JSON 行;空闲自退出;GPU 独占由队列保证"""

    def __init__(self):
        self.proc = None
        self.wlock = threading.Lock()   # stdin 写锁
        self.log = open(LOGS / "worker.log", "ab", buffering=0)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ensure(self):
        if self.alive():
            return
        self.log.write(f"\n[backend {iso()}] spawn worker\n".encode())
        self.proc = subprocess.Popen(
            [sys.executable, str(WEBUI / "engine_worker.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, cwd=str(PROJ),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        # 等 ready
        t0 = time.time()
        while time.time() - t0 < 30:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("提取引擎启动失败,详见 webui/data/logs/worker.log")
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                self.log.write(b"[worker][nonjson] " + line)
                continue
            if msg.get("type") == "ready":
                return
        raise RuntimeError("提取引擎启动超时")

    def send(self, obj: dict):
        with self.wlock:
            if not self.alive():
                raise RuntimeError("提取引擎未运行")
            self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proc.stdin.flush()

    def cancel(self):
        try:
            self.send({"cmd": "cancel"})
        except Exception:
            pass

    def kill(self):
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None

    def request(self, req: dict, on_line=None) -> dict:
        """发送请求并读取到该 id 的 result;期间进度经 on_line 回调"""
        self.ensure()
        self.send(req)
        rid = req["id"]
        while True:
            line = self.proc.stdout.readline()
            if not line:
                self.kill()
                raise RuntimeError("提取引擎意外退出,详见 webui/data/logs/worker.log")
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if on_line:
                on_line(msg)
            if msg.get("id") == rid and msg.get("type") == "result":
                return msg

    def warm_async(self):
        """后台预热模型(启动时/嵌入完成后),保证提取请求秒级响应"""
        def _warm():
            try:
                self.request(dict(id=f"warm-{int(time.time())}", cmd="warm"))
                self.log.write(f"[backend {iso()}] worker warmed\n".encode())
            except Exception as e:
                self.log.write(f"[backend {iso()}] warm failed: {e}\n".encode())
        threading.Thread(target=_warm, daemon=True).start()


WORKER = EngineWorker()

# ---------------------------------------------------------------- 队列(GPU 串行)


class QueueWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="gpu-queue")

    def next_job(self):
        for j in JOBS.snapshot():
            if j["status"] == "queued":
                return j
        return None

    def run(self):
        while True:
            job = self.next_job()
            if job is None:
                time.sleep(0.4)
                continue
            try:
                (self.run_embed if job["kind"].startswith("embed") else self.run_extract)(job)
            except Exception as e:
                self.finish(job, "failed", error=f"{type(e).__name__}: {e}")

    # ---- 公共 ----

    def set_status(self, job, status, **kw):
        cur = next((x for x in JOBS.snapshot() if x["id"] == job["id"]), None)
        if cur is None:
            return None
        if cur["status"] in ("canceled",) and status != "canceled":
            return None  # 已被取消,放弃后续处理
        cur.update(status=status, **kw)
        with JOBS.lock:
            for i, x in enumerate(JOBS.jobs):
                if x["id"] == job["id"]:
                    JOBS.jobs[i] = cur
        JOBS.save(force=status in ("done", "failed", "canceled", "interrupted"))
        return cur

    def set_progress(self, job, done=None, total=None, phase=None):
        cur = next((x for x in JOBS.snapshot() if x["id"] == job["id"]), None)
        if cur is None:
            return
        p = cur["progress"]
        if done is not None:
            p["done"] = done
        if total is not None:
            p["total"] = total
        if phase is not None:
            p["phase"] = phase
        if p.get("total") and p.get("done"):
            p["pct"] = round(100 * p["done"] / p["total"], 1)
            if cur["started_at"] and cur["status"] == "running":
                el = time.time() - datetime.fromisoformat(cur["started_at"]).timestamp()
                p["eta_s"] = max(0, round(el / p["done"] * (p["total"] - p["done"])))
        if done == 0 or phase:
            p.setdefault("eta_s", p.get("eta_s"))
        with JOBS.lock:
            for i, x in enumerate(JOBS.jobs):
                if x["id"] == job["id"]:
                    JOBS.jobs[i] = cur
        JOBS.save()

    def finish(self, job, status, result=None, error=""):
        cur = self.set_status(job, status, result=result or {}, error=error,
                              finished_at=iso())
        if cur:
            cur["progress"]["phase"] = dict(done="完成", failed="失败", canceled="已取消",
                                            interrupted="已中断")[status]
            with JOBS.lock:
                for i, x in enumerate(JOBS.jobs):
                    if x["id"] == job["id"]:
                        JOBS.jobs[i] = cur
            JOBS.save(force=True)

    def requested_cancel(self, job_id) -> bool:
        return any(x["id"] == job_id and x.get("cancel_req") for x in JOBS.snapshot())

    # ---- 嵌入 ----

    def run_embed(self, job):
        p = job["params"]
        work = next((w for w in cb_works() if w["id_hex"] == p["work_id"]), None)
        if work is None:
            return self.finish(job, "failed", error="所选作品已被删除,请重新选择")
        # 释放提取 worker 显存 -> GPU 严格串行
        WORKER.kill()
        cmd = [sys.executable, str(WEBUI / "engine_embed.py"),
               "--text", work["text"], "--work-name", work["name"],
               "--scaling-w", str(p["scaling_w"]), "--crf", str(p["crf"]),
               "--comp", str(p.get("comp", 0.0))]
        if p["kind"] == "video":
            cmd += ["--input", p["sources"][0]]
        else:
            cmd += ["--images", *p["sources"]]
        log_f = open(LOGS / f"{job['id']}.log", "ab", buffering=0)
        log_f.write(f"[backend {iso()}] {' '.join(cmd)}\n".encode())
        self.set_status(job, "running", started_at=iso())
        self.set_progress(job, phase="加载模型")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, cwd=str(PROJ),
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        job_real = job
        result, error, got_done = {}, "", False
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                log_f.write(raw)
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    error = error or line  # 引擎报错栈
                    continue
                t = msg.get("type")
                if t == "start":
                    self.set_progress(job, done=0, total=msg.get("total") or 0,
                                      phase="嵌入中" if msg["kind"] == "video" else "嵌入图片")
                elif t == "progress":
                    self.set_progress(job, done=msg["done"], total=msg["total"])
                elif t == "psnr":
                    result["psnr"] = msg
                elif t == "selfcheck":
                    result.setdefault("selfchecks", []).append(msg)
                elif t == "item":
                    result.setdefault("items", []).append(msg)
                elif t == "part":
                    with JOBS.lock:
                        for i, x in enumerate(JOBS.jobs):
                            if x["id"] == job["id"]:
                                JOBS.jobs[i]["part"] = msg["path"]
                elif t == "done":
                    got_done = True
                    result.update({k: v for k, v in msg.items() if k != "type"})
                elif t == "error":
                    error = msg["message"]
                if self.requested_cancel(job["id"]):
                    break
            rc = proc.wait()
        finally:
            log_f.close()
            if proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True)
        cur = next((x for x in JOBS.snapshot() if x["id"] == job["id"]), None) or {}
        if cur.get("cancel_req"):
            self._cleanup_part(cur)
            return self.finish(job_real, "canceled", error="运行中被取消,已清理半成品")
        if rc == 0 and got_done:
            self.finish(job_real, "done", result=result)
            WORKER.warm_async()  # 嵌入完成后回填预热,提取即刻秒级可用
            return
        self._cleanup_part(cur)
        self.finish(job_real, "failed", error=error or f"引擎退出码 {rc}")

    @staticmethod
    def _cleanup_part(cur):
        part = cur.get("part")
        if part and Path(part).exists():
            try:
                Path(part).unlink()
            except OSError:
                pass

    # ---- 提取 ----

    def run_extract(self, job):
        p = job["params"]
        self.set_status(job, "running", started_at=iso())
        self.set_progress(job, done=0, total=0, phase="准备提取引擎")
        req = dict(id=job["id"], cmd={"image": "extract_image", "video": "extract_video",
                                      "scan": "extract_scan"}[p["mode"]], path=p["path"])
        if p["mode"] == "video":
            req.update(t=p["t"], window=p["window"])
        if p["mode"] == "scan":
            req.update(interval=p["interval"])

        def on_line(msg):
            if msg.get("type") == "model_loaded":
                self.set_progress(job, phase="提取中")
            elif msg.get("id") == job["id"] and msg.get("type") == "progress":
                self.set_progress(job, done=msg["done"], total=msg.get("total") or msg["done"])

        try:
            payload = WORKER.request(req, on_line)
        except RuntimeError as e:
            if self.requested_cancel(job["id"]):
                return self.finish(job, "canceled", error="已取消")
            return self.finish(job, "failed", error=str(e))
        if self.requested_cancel(job["id"]):
            return self.finish(job, "canceled", error="已取消")
        if payload.get("status") == "error":
            return self.finish(job, "failed", error=payload.get("message", "提取失败"))
        self.set_progress(job, done=1, total=1, phase="完成")
        self.finish(job, "done", result=payload)


QueueWorker().start()
WORKER.warm_async()  # 启动即后台预热提取模型(R3.2 秒级响应)

# ---------------------------------------------------------------- 系统自检(缓存)

_sysinfo = {"torch": None, "ffmpeg": None, "gpu": None, "gpu_ts": 0.0}
_syslock = threading.Lock()


def _probe_torch():
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             "import torch;print('1' if torch.cuda.is_available() else '0');"
             "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"],
            capture_output=True, text=True, timeout=120)
        lines = r.stdout.strip().splitlines()
        ok = lines and lines[0].strip() == "1"
        return dict(cuda=bool(ok), name=lines[1].strip() if ok and len(lines) > 1 else "")
    except Exception as e:
        return dict(cuda=False, name="", error=str(e))


def _probe_gpu():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8)
        name, total, used = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        return dict(ok=True, name=name, total_mb=int(total), used_mb=int(used))
    except Exception:
        return dict(ok=False)


def system_status():
    with _syslock:
        if _sysinfo["torch"] is None:
            _sysinfo["torch"] = dict(cuda=None, name="", note="检测中…")
            threading.Thread(target=lambda: _sysinfo.__setitem__(
                "torch", _probe_torch()), daemon=True).start()
        if _sysinfo["ffmpeg"] is None:
            try:
                subprocess.run([C.FF, "-version"], capture_output=True, timeout=10)
                _sysinfo["ffmpeg"] = True
            except Exception:
                _sysinfo["ffmpeg"] = False
        if time.time() - _sysinfo["gpu_ts"] > 5:
            _sysinfo["gpu"] = _probe_gpu()
            _sysinfo["gpu_ts"] = time.time()
    disk = shutil.disk_usage(PROJ)
    jobs = JOBS.snapshot()
    return dict(
        gpu=_sysinfo["gpu"], torch=_sysinfo["torch"], ffmpeg_ok=_sysinfo["ffmpeg"],
        weights_ok=(C.PROJ / "third_party" / "watermark-anything" / "checkpoints" /
                    "wam_mit.pth").exists(),
        codebook_count=len(cb_works()),
        queue=dict(running=sum(1 for j in jobs if j["status"] == "running"),
                   queued=sum(1 for j in jobs if j["status"] == "queued")),
        disk=dict(free_gb=round(disk.free / 2**30, 1), total_gb=round(disk.total / 2**30, 1)),
        data_dir=str(C.DATA), output_dir=str(C.OUTPUT),
        ts=iso())


# ---------------------------------------------------------------- API:系统

@app.get("/api/state")
def api_state():
    jobs = JOBS.snapshot()
    running = next((j for j in jobs if j["status"] == "running"), None)
    queued = [dict(id=j["id"], label=j["label"], kind=j["kind"]) for j in jobs
              if j["status"] == "queued"]
    return dict(running=None if not running else dict(
        id=running["id"], label=running["label"], kind=running["kind"],
        progress=running["progress"]), queued=queued,
        queue_len=len(queued) + (1 if running else 0))


@app.get("/api/overview")
def api_overview():
    return system_status()


# ---------------------------------------------------------------- API:作品码本


def _usage_count(id_hex: str) -> int:
    n = 0
    for m in C.OUTPUT.glob("*_meta.json"):
        try:
            if json.loads(m.read_text(encoding="utf-8")).get("id_hex") == id_hex:
                n += 1
        except Exception:
            pass
    return n


@app.get("/api/works")
def api_works():
    ws = []
    for w in cb_works():
        w = dict(w)
        w["usage"] = _usage_count(w["id_hex"])
        ws.append(w)
    return ws


@app.post("/api/works")
async def api_works_add(req: Request):
    b = await req.json()
    try:
        w = add_work(b.get("name", ""), b.get("text", ""), b.get("note", ""))
    except CodebookError as e:
        raise HTTPException(400, str(e))
    return dict(w, usage=0)


@app.patch("/api/works/{id_hex}")
async def api_works_edit(id_hex: str, req: Request):
    b = await req.json()
    try:
        return edit_work(id_hex, b.get("name"), b.get("note"))
    except CodebookError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/works/{id_hex}")
def api_works_del(id_hex: str):
    try:
        return delete_work(id_hex)
    except CodebookError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- API:浏览/探测/上传/预览


@app.get("/api/browse")
def api_browse(path: str = None):
    if not path:
        drives = []
        for L in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            p = Path(f"{L}:\\")
            if p.exists():
                drives.append(dict(name=f"{L}:", path=str(p)))
        return dict(drives=drives, parent=None, path=None, dirs=[], files=[])
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"路径不存在: {path}")
    if p.is_file():
        p = p.parent
    dirs, files = [], []
    try:
        for e in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if e.is_dir():
                dirs.append(dict(name=e.name, path=str(e)))
            elif e.suffix.lower() in MEDIA_EXTS:
                try:
                    files.append(dict(name=e.name, path=str(e),
                                      size_mb=round(e.stat().st_size / 2**20, 1)))
                except OSError:
                    pass
    except PermissionError:
        raise HTTPException(403, "无权限访问该目录")
    parent = str(p.parent) if p.parent != p else None
    return dict(drives=None, parent=parent, path=str(p), dirs=dirs, files=files,
                cur_name=p.name or str(p))


@app.get("/api/probe")
def api_probe(path: str):
    p = Path(path)
    check_media_path(p)
    info = ffprobe_full(p)
    info.update(name=p.name, path=str(p), size_mb=round(p.stat().st_size / 2**20, 1),
                kind="video" if p.suffix.lower() in C.VIDEO_EXTS else "image")
    if info["kind"] == "video" and not info["fps"]:
        info["fps"] = 30.0
    return info


@app.post("/api/upload")
async def api_upload(request: Request, name: str):
    fname = safe_name(name)
    check_media_path(Path(fname), must_exist=False)
    dest = C.DATA / fname
    k = 1
    while dest.exists():  # 不覆盖已上传文件
        dest = C.DATA / f"{Path(fname).stem}({k}){Path(fname).suffix}"
        k += 1
    size = 0
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            size += len(chunk)
    return dict(path=str(dest), name=dest.name, size=size, size_mb=round(size / 2**20, 1))


_frame_cache = {}


@app.get("/api/frame")
def api_frame(path: str, t: float = 0.0):
    p = Path(path)
    check_media_path(p)
    if p.suffix.lower() not in C.VIDEO_EXTS:
        raise HTTPException(400, "仅视频可取预览帧")
    key = (str(p), round(t * 2) / 2)
    if key in _frame_cache:
        return Response(_frame_cache[key], media_type="image/jpeg")
    r = subprocess.run(
        [C.FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{max(t, 0):.3f}",
         "-i", str(p), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", "-f", "mjpeg",
         "pipe:"],
        capture_output=True, timeout=60)
    if r.returncode != 0 or not r.stdout:
        raise HTTPException(400, "取帧失败")
    if len(_frame_cache) > 80:
        _frame_cache.clear()
    _frame_cache[key] = r.stdout
    return Response(r.stdout, media_type="image/jpeg")


V_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
          ".webm": "video/webm", ".avi": "video/x-msvideo", ".flv": "video/x-flv"}


def _check_preview_path(p: Path):
    """预览/对比只允许 data\\ 与 output\\ 内的媒体文件"""
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    rp = str(p.resolve())
    if not (rp.startswith(str(C.DATA.resolve())) or rp.startswith(str(C.OUTPUT.resolve()))):
        raise HTTPException(403, "仅允许预览 data\\ 与 output\\ 内文件")
    if p.suffix.lower() not in MEDIA_EXTS:
        raise HTTPException(400, "不支持的媒体格式")


@app.get("/api/raw")
def api_raw(path: str):
    """媒体原文件(图片预览 / 视频播放,支持 Range 供 <video> 拖动)"""
    p = Path(path)
    _check_preview_path(p)
    ext = p.suffix.lower()
    if ext in C.IMG_EXTS:
        return FileResponse(p)
    return FileResponse(p, media_type=V_MIME.get(ext, "application/octet-stream"))


_probe_cache: dict = {}


def _decode_frame_rgb(p: Path, t: float):
    """ffmpeg 取单帧 -> RGB ndarray(供差异图计算);探测结果按文件缓存"""
    import numpy as np
    key = str(p)
    info = _probe_cache.get(key)
    if info is None:
        info = ffprobe_full(p)
        _probe_cache[key] = info
    w, h = info["w"], info["h"]
    r = subprocess.run(
        [C.FF, "-hide_banner", "-loglevel", "error", "-nostdin",
         "-ss", f"{max(t, 0):.3f}", "-i", str(p), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=90)
    need = w * h * 3
    if r.returncode != 0 or len(r.stdout) < need:
        raise HTTPException(400, f"取帧失败: {p.name}")
    return np.frombuffer(r.stdout[:need], np.uint8).reshape(h, w, 3)


@app.get("/api/diff")
def api_diff(path: str, source: str, t: float = 0.0, amp: float = 8.0):
    """同时间点 成品帧 vs 原片帧 的差异放大图(X-PSNR 头返回该帧 PSNR)"""
    import cv2
    import numpy as np
    a = _decode_frame_rgb(Path(source), t)
    b = _decode_frame_rgb(Path(path), t)
    if a.shape != b.shape:
        raise HTTPException(400, "两文件分辨率不一致,无法逐帧对比")
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    mse = float((d.astype(np.float64) ** 2).mean())
    psnr = 99.0 if mse == 0 else 10 * math.log10(255.0 ** 2 / mse)
    vis = np.clip(d * float(amp), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(vis, cv2.COLOR_RGB2BGR),
                           [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return Response(buf.tobytes(), media_type="image/jpeg",
                    headers={"X-PSNR": f"{psnr:.2f}", "Cache-Control": "no-store"})


# ---- 浏览器放不了的源(如 HEVC)一次性 CPU 转码缓存(不占 GPU) ----
PREVIEW_CACHE = DATA_DIR / "preview_cache"
PREVIEW_CACHE.mkdir(parents=True, exist_ok=True)
_prev_status: dict = {}
_prev_lock = threading.Lock()


def _preview_cache_path(p: Path) -> Path:
    return PREVIEW_CACHE / f"{p.stem}_{abs(hash(str(p))) % 99999:05d}.mp4"


def _transcode_worker(p: Path, out: Path):
    tmp = out.with_suffix(".tmp.mp4")
    try:
        r = subprocess.run(
            [C.FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(p),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)],
            capture_output=True, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[-300:])
        tmp.replace(out)
        _prev_status[str(p)] = dict(status="ready")
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        _prev_status[str(p)] = dict(status="failed", error=str(e)[:300])


@app.get("/api/transcode_preview")
def api_transcode_preview(path: str, start: int = 0):
    p = Path(path)
    _check_preview_path(p)
    if p.suffix.lower() not in C.VIDEO_EXTS:
        raise HTTPException(400, "仅视频需要转码预览")
    out = _preview_cache_path(p)
    if out.exists() and out.stat().st_size > 0:
        return dict(status="ready", url="/api/raw?path=" + _qurl(str(out)))
    st = _prev_status.get(str(p))
    if not start:
        return dict(status=st["status"] if st else "none")
    if st and st.get("status") == "running":
        return dict(status="running")
    with _prev_lock:
        _prev_status[str(p)] = dict(status="running")
        threading.Thread(target=_transcode_worker, args=(p, out), daemon=True).start()
    return dict(status="running")


def _qurl(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


# ---------------------------------------------------------------- API:任务


@app.post("/api/jobs/embed")
async def api_jobs_embed(req: Request):
    b = await req.json()
    work_id = b.get("work_id")
    if not work_id or not any(w["id_hex"] == work_id for w in cb_works()):
        raise HTTPException(400, "请选择有效作品")
    sw = float(b.get("scaling_w", 2.0))
    if not (1.0 <= sw <= 4.0):
        raise HTTPException(400, "嵌入强度须在 1.0~4.0")
    crf = int(b.get("crf", 14))
    if not (8 <= crf <= 34):
        raise HTTPException(400, "crf 须在 8~34")
    comp = float(b.get("comp", 0.0))
    if not (0 <= comp <= 2):
        raise HTTPException(400, "色彩回补 comp 须在 0~2")
    sources = [str(Path(s)) for s in b.get("sources", [])]
    if not sources:
        raise HTTPException(400, "未选择素材")
    for s in sources:
        check_media_path(Path(s))
    kinds = set(Path(s).suffix.lower() for s in sources)
    made = []
    if any(k in C.VIDEO_EXTS for k in kinds) and any(k in C.IMG_EXTS for k in kinds):
        raise HTTPException(400, "视频与图片请分开提交(视频逐个排队,图片批量)")
    if any(k in C.VIDEO_EXTS for k in kinds):
        if len(sources) > 1:
            for s in sources:  # 多视频逐个排队(R2.7)
                made.append(JOBS.create("embed_video", f"打水印 · {Path(s).name}",
                                        dict(kind="video", sources=[s], work_id=work_id,
                                             crf=crf, scaling_w=sw, comp=comp)))
        else:
            made.append(JOBS.create("embed_video", f"打水印 · {Path(sources[0]).name}",
                                    dict(kind="video", sources=sources, work_id=work_id,
                                         crf=crf, scaling_w=sw, comp=comp)))
    else:
        made.append(JOBS.create("embed_images",
                                f"打水印 · {len(sources)} 张图片",
                                dict(kind="images", sources=sources, work_id=work_id,
                                     crf=crf, scaling_w=sw, comp=comp)))
    return [dict(id=j["id"], label=j["label"]) for j in made]


@app.post("/api/jobs/extract")
async def api_jobs_extract(req: Request):
    b = await req.json()
    mode = b.get("mode")
    if mode not in ("image", "video", "scan"):
        raise HTTPException(400, "未知提取模式")
    p = Path(str(b.get("path", "")))
    check_media_path(p)
    params = dict(mode=mode, path=str(p))
    if mode == "image" and p.suffix.lower() not in C.IMG_EXTS:
        raise HTTPException(400, "图片提取需要图片文件(png/jpg);视频请用「视频时间点」")
    if mode in ("video", "scan") and p.suffix.lower() not in C.VIDEO_EXTS:
        raise HTTPException(400, "该模式需要视频文件")
    if mode == "video":
        t = float(b.get("t", 0))
        window = float(b.get("window", 0.5))
        if not (0 <= window <= 5):
            raise HTTPException(400, "邻域窗口须在 0~5 秒")
        params.update(t=max(t, 0), window=window)
    if mode == "scan":
        params["interval"] = min(max(float(b.get("interval", 5)), 1), 60)
    labels = {"image": "提取 · ", "video": "时间点提取 · ", "scan": "全片扫描 · "}
    job = JOBS.create(f"extract_{mode}", labels[mode] + p.name, params)
    return dict(id=job["id"], label=job["label"])


@app.get("/api/jobs")
def api_jobs(limit: int = 200):
    return list(reversed(JOBS.snapshot()))[:limit]


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    return JOBS.get(job_id)


@app.get("/api/jobs/{job_id}/log")
def api_job_log(job_id: str):
    f = LOGS / f"{job_id}.log"
    if not f.exists():
        return ""
    data = f.read_text(encoding="utf-8", errors="replace")
    return data[-20000:]


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str):
    job = JOBS.get(job_id)
    if job["status"] in ("done", "failed", "canceled"):
        raise HTTPException(400, f"任务已{job['status']},无法取消")
    with JOBS.lock:
        job["cancel_req"] = True
        if job["status"] == "queued":
            job["status"] = "canceled"
            job["error"] = "排队中取消"
            job["finished_at"] = iso()
    JOBS.save(force=True)
    if job["status"] == "running":
        if job["kind"].startswith("extract"):
            WORKER.cancel()
        # 嵌入:队列线程检测 cancel_req 后终止子进程
    return dict(ok=True)


@app.post("/api/jobs/{job_id}/requeue")
def api_job_requeue(job_id: str):
    job = JOBS.get(job_id)
    if job["status"] not in ("interrupted", "failed", "canceled"):
        raise HTTPException(400, "仅中断/失败/取消的任务可重新排队")
    with JOBS.lock:
        job["status"] = "queued"
        job["cancel_req"] = False
        job["error"] = ""
        job["result"] = {}
        job["progress"] = dict(done=0, total=0, phase="排队中", pct=0, eta_s=None)
        job["started_at"] = None
        job["finished_at"] = None
    JOBS.save(force=True)
    return dict(ok=True)


@app.get("/api/jobs/{job_id}/export")
def api_job_export(job_id: str):
    job = JOBS.get(job_id)
    if not job["kind"].startswith("extract"):
        raise HTTPException(400, "仅提取任务可导出取证 JSON")
    payload = dict(job=dict(id=job["id"], kind=job["kind"], label=job["label"],
                            params=job["params"], created_at=job["created_at"],
                            started_at=job["started_at"], finished_at=job["finished_at"]),
                   result=job.get("result", {}))
    return JSONResponse(payload, headers={
        "Content-Disposition": f"attachment; filename=forensic_{job_id}.json"})


# ---------------------------------------------------------------- API:成品库


_probe_cache = {}


@app.get("/api/products")
def api_products():
    items = []
    for f in C.OUTPUT.iterdir():
        if f.name.startswith(".part-") or f.suffix.lower() not in (".mp4", ".png"):
            continue
        if "_已加水印" not in f.name:
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        key = (f.name, st.st_mtime, st.st_size)
        if key not in _probe_cache:
            try:
                info = ffprobe_full(f)
            except HTTPException:
                info = dict(codec="?", w=0, h=0)
            meta = {}
            mp = f.with_name(f.stem + "_meta.json")
            if mp.exists():
                try:
                    meta = json.loads(mp.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            _probe_cache[key] = dict(info=info, meta=meta)
        cached = _probe_cache[key]
        src = (cached["meta"] or {}).get("source") or ""
        if not src:  # 命名约定兜底: X_已加水印_v2.ext -> data\X.ext
            stem = re.sub(r"_v\d+$", "", f.stem)
            stem = re.sub(r"_已加水印$", "", stem)
            cand = C.DATA / (stem + f.suffix)
            src = str(cand) if cand.exists() else ""
        source_exists = bool(src) and Path(src).exists()
        items.append(dict(name=f.name, path=str(f), size_mb=round(st.st_size / 2**20, 1),
                          mtime=int(st.st_mtime),
                          modified=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                          kind="video" if f.suffix == ".mp4" else "image",
                          source=src, source_exists=source_exists,
                          **cached["info"], meta=cached["meta"]))
    items.sort(key=lambda x: -x["mtime"])
    return items


@app.post("/api/open")
async def api_open(req: Request):
    b = await req.json()
    p = Path(b.get("path", ""))
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    if not under_proj(p):
        raise HTTPException(403, "仅允许打开项目目录内文件")
    os.startfile(str(p))  # 系统默认播放器/看图器(R4.1)
    return dict(ok=True)


@app.get("/api/download")
def api_download(path: str):
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    if not (under_proj(p) and str(p.resolve()).startswith(str(C.OUTPUT.resolve()))):
        raise HTTPException(403, "仅允许下载 output\\ 内成品")
    return FileResponse(p, filename=p.name)


# ---------------------------------------------------------------- 静态页

START_TS = str(int(time.time()))  # 资源版本号:每次重启自动破浏览器缓存


class NoCacheStatic:
    """纯 ASGI 中间件:仅给静态页加 no-cache。
    不用 BaseHTTPMiddleware——它会把 FileResponse 流包进内存队列,视频 range 供流吞吐暴跌。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (scope["path"] == "/" or scope["path"].startswith("/static")):
            async def send_with_header(message):
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers.append("Cache-Control", "no-cache")
                await send(message)
            await self.app(scope, receive, send_with_header)
        else:
            await self.app(scope, receive, send)


app.add_middleware(NoCacheStatic)


@app.get("/")
def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__VER__", START_TS))


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import asyncio
    import uvicorn
    import shutil as _sh
    if sys.platform == "win32":
        # Proactor 循环在客户端突然断开时会在 connection_lost 回调里抛
        # ConnectionResetError 并带崩进程;Selector 循环无此问题
        # (本项目子进程全是线程池内的阻塞调用,不依赖 Proactor 的异步子进程)。
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if not Path(C.FF).exists() and not _sh.which(C.FF):
        print("[警告] 未找到 ffmpeg:打水印/预览会失败。运行 webui\\安装环境.bat 自动下载到项目 bin\\,"
              "或在 webui\\local_config.bat 里 set \"WM_FFMPEG=你的ffmpeg目录\"", flush=True)
    print(f"wm2 水印工作台 -> http://127.0.0.1:{PORT}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
