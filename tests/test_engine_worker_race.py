#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EngineWorker 并发回归测试(P1:预热与提取并发读 stdout 互相吞行 -> 请求永久挂起)。

直接解析 webui/app.py 提取 EngineWorker 类定义(不 import app.py,避免触发 FastAPI/
热预热/GPU 初始化),用假 worker 进程按 JSON 行协议模拟 engine_worker,验证:
  1) 启动等待超时一次(ready 慢)不遗留阻塞读线程——旧实现每次超时泄露一个读线程,
     它会抢走首个请求的首行响应,导致请求永不返回;
  2) 并发 request 各自拿到自己的 result,不吞行不挂起;
  3) 请求在途时 kill worker -> request 快速报错并释放锁,后续请求能正常拉起新进程;
  4) 首请求前有额外的无 id 行(如 model_loaded)时能正确跳过。

--real: 不 mock subprocess,真拉起 webui/engine_worker.py(需要 numpy+cv2,只需"ping"
命令,不加载模型/GPU)。默认仅标准库,任何 Python 3 可跑。

运行: python tests/test_engine_worker_race.py [--real]
"""
import argparse
import ast
import json
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "webui" / "data" / "logs"
LOGS.mkdir(parents=True, exist_ok=True)

REQ_TIMEOUT = 15.0


def load_engine_worker_class(subprocess):
    """从 webui/app.py 只提取 EngineWorker 类(独立于 FastAPI/模块级副作用)"""
    src = (ROOT / "webui" / "app.py").read_text(encoding="utf-8")
    mod = ast.parse(src)
    cls = next(n for n in mod.body if isinstance(n, ast.ClassDef) and n.name == "EngineWorker")
    ns = dict(
        queue=queue, os=__import__("os"), sys=sys, subprocess=subprocess,
        threading=threading, time=time, json=json,
        iso=lambda: datetime.now().isoformat(timespec="seconds"),
        LOGS=LOGS, WEBUI=ROOT / "webui", PROJ=ROOT,
    )
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "<app:EngineWorker>", "exec"), ns)
    return ns["EngineWorker"]


# ---------------- 假 worker:按 JSON 行协议模拟 engine_worker ----------------

class _FakeIn:
    """假 stdin:send() 写入的请求行进队列,驱动线程消费"""

    def __init__(self):
        self.q = queue.Queue()

    def write(self, b):
        self.q.put(b)

    def flush(self):
        pass


class _FakeOut:
    """假 stdout:支持 'for line in stream' 阻塞迭代"""

    def __init__(self):
        self.q = queue.Queue()

    def push(self, b):
        self.q.put(b)

    def eof(self):
        self.q.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        x = self.q.get()
        if x is None:
            raise StopIteration
        return x


class _FakeProc:
    def __init__(self, cfg):
        self.cfg = cfg
        self.stdin = _FakeIn()
        self.stdout = _FakeOut()
        self._dead = False
        threading.Thread(target=self._run, daemon=True, name="fake-worker").start()

    def _run(self):
        # 模拟引擎启动开销(import torch/cv2):ready 前无输出
        time.sleep(self.cfg["ready_delay"])
        self.stdout.push(b'{"type":"ready"}\n')
        first = True
        while True:
            try:
                raw = self.stdin.q.get(timeout=60)
            except queue.Empty:
                continue
            if raw is None:
                break
            try:
                msg = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if first:  # 首个请求前补一条无 id 行,模拟真实引擎的 model_loaded
                first = False
                self.stdout.push(b'{"type":"model_loaded"}\n')
            if msg.get("cmd") == "slow":  # 长在途请求(供 kill 测试)
                time.sleep(self.cfg["slow_delay"])
            else:
                time.sleep(self.cfg["reply_delay"])
            self.stdout.push(json.dumps(
                dict(id=msg.get("id"), type="result", status="pong")).encode() + b"\n")

    def poll(self):
        return 0 if self._dead else None

    def kill(self):
        self._dead = True
        self.stdout.eof()

    def wait(self, timeout=None):
        return 0


class _FakeSubprocess:
    """替换 app.py 命名空间里的 subprocess:Popen 返回假进程(每次 spawn 新实例)"""

    PIPE = -1
    DEVNULL = -2

    def __init__(self, cfg):
        self.cfg = cfg
        self.spawns = 0

    def Popen(self, *a, **kw):
        self.spawns += 1
        return _FakeProc(self.cfg)


# ---------------- 测试工具 ----------------

def run_request(w, req):
    box = {}

    def _do():
        try:
            box["ok"] = w.request(req)
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout=REQ_TIMEOUT)
    return box


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  [PASS] {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="真拉起 engine_worker.py(需 numpy/cv2)")
    args = ap.parse_args()
    if args.real:
        import subprocess  # 真进程
        EngineWorker = load_engine_worker_class(subprocess)
    else:
        cfg = dict(ready_delay=1.5, reply_delay=0.02, slow_delay=30.0)
        EngineWorker = load_engine_worker_class(_FakeSubprocess(cfg))

    w = EngineWorker()
    try:
        # --- 1) 慢 ready(旧实现:超时一次即泄露读线程,首个 ping 必挂) + 并发 12 请求 ---
        print("[T1] 慢 ready + 并发 12 个 ping 请求")
        ids = [dict(id=f"ping-{i}", cmd="ping") for i in range(12)]
        done = []

        def _req(i):
            r = run_request(w, ids[i])
            if "err" in r:
                done.append(("err", i, r["err"]))
            elif r["ok"].get("status") == "pong" and r["ok"].get("id") == ids[i]["id"]:
                done.append(("ok", i, None))
            else:
                done.append(("bad", i, r["ok"]))

        ts = [threading.Thread(target=_req, args=(i,), daemon=True) for i in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=REQ_TIMEOUT + 2)
        n_ok = sum(1 for s, _, _ in done if s == "ok")
        check(n_ok == 12, f"12 个并发 ping 全部拿到自己的 result(实际 {n_ok})")
        for s, i, e in done:
            check(s != "err", f"请求 {i} 无异常" if s == "ok" else f"请求 {i} 出错: {e}")
            check(s != "bad", f"请求 {i} 返回正确 id" if s == "ok" else f"请求 {i} 返回了错误 id")

        # --- 2) 首请求前的 model_loaded 行被正确跳过(上面已隐含,单独再验证一次) ---
        print("[T2] 额外无 id 行(model_loaded)跳过")
        r = run_request(w, dict(id="misc-1", cmd="ping"))
        check("ok" in r and r["ok"]["status"] == "pong" and r["ok"]["id"] == "misc-1",
              f"带 model_loaded 前缀的请求仍拿到自己的结果: {r.get('ok') or r.get('err')}")

        # --- 3) 在途请求 kill worker -> request 快速报错,后续请求可拉起新进程 ---
        #     真实引擎无 "slow" 命令(未知命令会立即回 error),仅假 worker 可模拟长在途;
        #     --real 下退化为:kill 空闲 worker 后,新请求能自动重启并成功。
        print("[T3] kill 在途请求 -> 报错并释放锁 -> 新请求自动重启 worker")
        if not args.real:
            box = {}

            def _do_slow():
                try:
                    box["ok"] = w.request(dict(id="slow-1", cmd="slow"))
                except Exception as e:
                    box["err"] = e

            t = threading.Thread(target=_do_slow, daemon=True)
            t.start()
            time.sleep(0.5)          # 让 slow 请求进入在途状态
            w.kill()
            t.join(timeout=5)
            check("err" in box and isinstance(box["err"], RuntimeError) and "意外退出" in str(box["err"]),
                  f"在途请求捕获到引擎退出: {box.get('err')}")
        else:
            w.kill()  # 空闲状态直接杀,验证 kill 后 ensure 可重启
            check(w.proc is None or not w.alive(), "kill 后 worker 已停止")
        # 锁已释放:后续请求应立即成功(重启新进程)
        r = run_request(w, dict(id="after-kill", cmd="ping"))
        check("ok" in r and r["ok"]["status"] == "pong",
              f"kill 后新请求经 ensure 重启并成功: {r.get('ok') or r.get('err')}")
    finally:
        w.kill()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
