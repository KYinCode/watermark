#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wmlog 单元自检:引擎 stdout 零污染 / 级别开关 / backend 轮转 / CLI 双写 / 启动清理。
纯标准库,任何 Python 3 可跑(不碰 torch/GPU)。

运行: python tests/test_wmlog.py
"""
import contextlib
import io
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import wmlog


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  [PASS] {msg}")


def t1_engine_stdout_purity():
    print("[T1] setup_engine:日志只进 stderr,stdout 保持纯协议通道")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        wmlog.setup_engine()
        logging.getLogger("wm.embed").info("hello engine")
    check(out.getvalue() == "", f"stdout 无任何输出(实际 {out.getvalue()!r})")
    check("hello engine" in err.getvalue() and "INFO" in err.getvalue()
          and "wm.embed" in err.getvalue(), "stderr 含 时间戳/级别/模块名 的格式化行")


def t2_level_env():
    print("[T2] WM2_LOG_LEVEL=ERROR 时 INFO 被过滤、ERROR 正常输出")
    os.environ["WM2_LOG_LEVEL"] = "ERROR"
    try:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            wmlog.setup_engine()
            logging.getLogger("wm.embed").info("should not appear")
            logging.getLogger("wm.embed").error("must appear")
        check("should not appear" not in err.getvalue(), "INFO 被级别过滤")
        check("must appear" in err.getvalue(), "ERROR 正常输出")
    finally:
        os.environ.pop("WM2_LOG_LEVEL", None)


def t3_backend_rotation():
    print("[T3] setup_backend:超 maxBytes 触发轮转,backend.log.1 产生")
    tmp = Path(tempfile.mkdtemp(prefix="wmlog_t3_"))
    try:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            wmlog.setup_backend(tmp, max_bytes=600, backup_count=2)
            lg = logging.getLogger("wm.app")
            for i in range(30):
                lg.info("x" * 50 + f" {i}")
        check((tmp / "backend.log").exists(), "backend.log 生成")
        check((tmp / "backend.log.1").exists(), "backend.log.1 轮转产物存在")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t4_cli_dual_write():
    print("[T4] setup_cli:DEBUG 只进文件,WARNING 文件+控制台裸格式")
    tmp = Path(tempfile.mkdtemp(prefix="wmlog_t4_"))
    try:
        f = tmp / "run.log"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            wmlog.setup_cli(f)
            logging.getLogger("wm.cli.t").debug("debug-flow")
            logging.getLogger("wm.cli.t").warning("warn-line")
        text = f.read_text(encoding="utf-8")
        check("debug-flow" in text and "warn-line" in text, "文件含 DEBUG+WARNING 带时间戳流水")
        check(out.getvalue().strip() == "warn-line",
              f"控制台只出 WARNING 裸行(实际 {out.getvalue()!r})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t5_cli_exc_console():
    print("[T5] setup_cli:异常记录在控制台附带堆栈(供 SystemExit 抑制解释器重复打印)")
    tmp = Path(tempfile.mkdtemp(prefix="wmlog_t5_"))
    try:
        f = tmp / "run.log"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            wmlog.setup_cli(f)
            try:
                raise ValueError("boom")
            except ValueError:
                logging.getLogger("wm.cli.t").exception("运行失败")
        console = out.getvalue()
        text = f.read_text(encoding="utf-8")
        check("运行失败" in console and "boom" in console and "ValueError" in console,
              "控制台含消息+堆栈")
        check("Traceback" in text, "文件含完整堆栈")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t6_cleanup_logs():
    print("[T6] cleanup_logs:只删 job_id 形态旧日志;worker.log 超限轮转")
    tmp = Path(tempfile.mkdtemp(prefix="wmlog_t6_"))
    try:
        old = tmp / "18f1a2b3c4d-abcd.log"
        new = tmp / "18f1a2b3c4d-ef01.log"
        keep = tmp / "backend.log"
        worker = tmp / "worker.log"
        for f in (old, new, keep, worker):
            f.write_text("x", encoding="utf-8")
        oldt = time.time() - 40 * 86400
        os.utime(old, (oldt, oldt))
        worker.write_text("x" * (11 * 1024 * 1024), encoding="utf-8")
        removed, rotated = wmlog.cleanup_logs(tmp, keep_days=30)
        check(removed == 1 and not old.exists(), "超期 job 日志被删")
        check(new.exists() and keep.exists(), "未超期 job 日志与 backend.log 保留")
        check(rotated and not worker.exists() and (tmp / "worker.log.1").exists(),
              "worker.log 超 10MB 轮转为 .1")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    t1_engine_stdout_purity()
    t2_level_env()
    t3_backend_rotation()
    t4_cli_dual_write()
    t5_cli_exc_console()
    t6_cleanup_logs()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
