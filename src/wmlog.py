# -*- coding: utf-8 -*-
"""wm2 统一日志基建(纯标准库,零新依赖)。

三种场景各一个入口,全项目共用一套格式 `时间 级别 [模块] 消息`:
  setup_backend(logs_dir)  WebUI 后端:backend.log 轮转文件 + stdout 控制台
                           (启动WebUI.bat 只把 stderr 重定向到 %TEMP%,stdout 留在启动窗口)
  setup_engine()           引擎子进程:只挂 stderr —— stdout 是 JSON 行协议通道,绝不占用;
                           stderr 由后端接管(engine_embed -> 任务日志,engine_worker -> worker.log)
  setup_cli(log_file)      CLI --log-file:文件旁路记 DEBUG 级时间戳流水;控制台只补 WARNING+
                           裸格式(CLI 的正常人类可读输出仍走 print,不受影响)

级别:环境变量 WM2_LOG_LEVEL = DEBUG/INFO/WARNING/ERROR(默认 INFO);
local_config.bat 可 set,引擎子进程经 app.py spawn 的 env 天然继承。
任何进程都没调用 setup 时(如 CLI 只 import common),Python lastResort 兜底把
WARNING+ 打到 stderr,行为与改造前的 print 基本等价,不会丢告警。
"""
import logging
import os
import re
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"

# 任务日志文件名(job_id 形如 18f...x-abcd);清理只删这种形态,不碰 backend/worker.log
_JOB_LOG_RE = re.compile(r"^[0-9a-f]+-[0-9a-f]{4}\.log$")

_VALID_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
                 "WARNING": logging.WARNING, "ERROR": logging.ERROR}


def env_level() -> int:
    raw = (os.environ.get("WM2_LOG_LEVEL") or "INFO").strip().upper()
    return _VALID_LEVELS.get(raw, logging.INFO)


def _install(handlers, level=None):
    """重置 root logger 的 handler 后统一安装(可重复调用,测试友好)。"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    fmt = logging.Formatter(FMT, DATEFMT)
    for h in handlers:
        if h.formatter is None:
            h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(env_level() if level is None else level)


def setup_backend(logs_dir, max_bytes=5 * 1024 * 1024, backup_count=3) -> None:
    """WebUI 后端。uvicorn 以 log_config=None 启动(见 app.py __main__),
    其日志(uvicorn.error 含 ASGI 异常堆栈)propagate 到 root,一并进 backend.log。"""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(logs_dir / "backend.log", maxBytes=max_bytes,
                             backupCount=backup_count, encoding="utf-8")
    ch = logging.StreamHandler(sys.stdout)
    _install([fh, ch])
    # 访问日志默认静默(单人本机自用);WM2_ACCESS_LOG=1 打开。非 2xx 由 app.py 的
    # AccessLog 中间件以 INFO 记录,不依赖 uvicorn.access。
    logging.getLogger("uvicorn.access").setLevel(
        logging.INFO if os.environ.get("WM2_ACCESS_LOG") == "1" else logging.WARNING)


def setup_engine() -> None:
    """引擎子进程:stderr-only。引擎绝不向 stdout 写非协议内容(写满管道会卡死后端队列)。"""
    _install([logging.StreamHandler(sys.stderr)])


def setup_cli(log_file) -> None:
    """CLI --log-file 模式:root 放开到 DEBUG 让流水全进文件;控制台仅 WARNING+ 兜底。"""
    p = Path(log_file)
    if p.parent and str(p.parent):
        p.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(p, encoding="utf-8")
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.WARNING)
    ch.setFormatter(_CliConsoleFormatter())  # 裸格式贴近原 [warn] print;异常带堆栈便于排查
    _install([fh, ch], level=logging.DEBUG)


class _CliConsoleFormatter(logging.Formatter):
    """控制台裸格式(只出消息文本);带 exc_info 的记录把堆栈原样附在消息后,
    配合调用方 raise SystemExit(1) 抑制解释器重复打印。"""

    def format(self, record):
        s = record.getMessage()
        if record.exc_info:
            tb = self.formatException(record.exc_info)
            if tb:
                s = s + "\n" + tb
        return s


def cleanup_logs(logs_dir, keep_days=30, worker_log_max_bytes=10 * 1024 * 1024):
    """启动清理(后端调用)。返回 (删除的任务日志数, worker.log 是否轮转)。

    只删 job_id 形态的任务日志(按 mtime 保留 keep_days 天),不碰 backend.log/worker.log;
    worker.log 超 worker_log_max_bytes 轮转为 worker.log.1(旧 .1 直接覆盖)。"""
    logs_dir = Path(logs_dir)
    cutoff = time.time() - keep_days * 86400
    removed = 0
    try:
        entries = list(logs_dir.iterdir())
    except OSError:
        entries = []
    for f in entries:
        try:
            if _JOB_LOG_RE.match(f.name) and f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    rotated = False
    wl = logs_dir / "worker.log"
    try:
        if wl.exists() and wl.stat().st_size > worker_log_max_bytes:
            wl.replace(logs_dir / "worker.log.1")
            rotated = True
    except OSError:
        pass
    return removed, rotated
