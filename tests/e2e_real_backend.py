#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实后端 E2E:打水印 -> 嵌入完成即刻连发提取(压"嵌入后预热 vs 首次提取"竞态窗口)。

前提: webui/app.py 已在 127.0.0.1:8765 以本仓库代码运行;GPU/模型权重就绪。
用法: <python> tests/e2e_real_backend.py <视频路径> <成品路径> [--backend http://127.0.0.1:8765]

流程: 新建测试作品 -> 提交嵌入任务并轮询至完成 -> 立刻提交 3 个提取任务
(0s/3s/6s,均在预热窗口内) -> 断言全部命中且 ID/文本一致 -> 删除测试作品。
退出码 0 = 全部通过;1 = 失败;2 = 超时(疑似挂起,即旧 bug 形态)。
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

BACKEND = "http://127.0.0.1:8765"
POLL = 0.5
EMBED_TIMEOUT = 900.0
EXTRACT_TIMEOUT = 600.0
WORK_NAME = "e2e-真实链路"
WORK_TEXT = "©E2E链路测试|2026-09-06|仅临时验证"


def call(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BACKEND + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def wait_state(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            s, _ = call("GET", "/api/state", timeout=5)
            if s == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("后端未就绪")


def poll_job(job_id, timeout, expect_done=True, on_progress=None):
    """轮询任务直至 done/failed/canceled 或超时;返回 (status, job, elapsed)"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        s, job = call("GET", f"/api/jobs/{job_id}")
        assert s == 200, f"查询任务失败: {job}"
        if on_progress and job["status"] != last:
            last = job["status"]
            on_progress(job)
        if job["status"] in ("done", "failed", "canceled"):
            return job["status"], job, time.time() - t0
        time.sleep(POLL)
    return "timeout", {}, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("product")
    args = ap.parse_args()

    wait_state()
    print(f"[e2e] 后端就绪 {BACKEND}")

    # 1) 测试作品
    s, w = call("POST", "/api/works", {"name": WORK_NAME, "text": WORK_TEXT})
    assert s == 200, f"建作品失败: {w}"
    work_id = w["id_hex"]
    print(f"[e2e] 作品 {WORK_NAME} id={work_id}")

    # 2) 嵌入
    s, made = call("POST", "/api/jobs/embed", {
        "kind": "video", "sources": [str(Path(args.video).resolve())],
        "work_id": work_id, "crf": 14, "scaling_w": 2.0, "comp": 0.5})
    assert s == 200 and made, f"提交嵌入失败: {made}"
    job_id = made[0]["id"]

    def prog(j):
        p = j.get("progress") or {}
        print(f"  [embed] {j['status']} {p.get('phase','')} {p.get('done')}/{p.get('total')} 帧", flush=True)

    st, job, el = poll_job(job_id, EMBED_TIMEOUT, on_progress=prog)
    if st != "done":
        print(f"[e2e] 嵌入未完成: {st} {job}"); sys.exit(1)
    out = job["result"]["out"]
    print(f"[e2e] 嵌入完成 {el:.1f}s -> {out}  result 键={list(job['result'])}")
    prep = job["result"].get("psnr")
    if prep:
        print(f"[e2e] PSNR mean={prep.get('mean')} min={prep.get('min')} 自检={job['result'].get('selfchecks')}")

    # 3) 立刻连发提取(嵌入完成即触发 warm_async,此时模型尚未热)
    extract_ids = []
    for delay in (0, 3, 6):
        if delay:
            time.sleep(delay)
        s, ex = call("POST", "/api/jobs/extract",
                     {"mode": "video", "path": str(Path(out).resolve()),
                      "t": 1.5, "window": 0.5})
        assert s == 200, f"提交提取失败: {ex}"
        extract_ids.append(ex["id"])
        print(f"[e2e] 已提交提取 #{ex['id']} (嵌入完成后 +{delay}s)")

    failed = 0
    for i, eid in enumerate(extract_ids):
        def prog(j):
            p = j.get("progress") or {}
            print(f"  [extract#{i}] {j['status']} {p.get('phase','')} {p.get('done')}/{p.get('total')}", flush=True)
        st, job, el = poll_job(eid, EXTRACT_TIMEOUT, on_progress=prog)
        if st == "timeout":
            print(f"[e2e] 提取#{i} 超时(>{EXTRACT_TIMEOUT:.0f}s)!! 疑似挂起"); failed += 1
            continue
        if st != "done":
            print(f"[e2e] 提取#{i} 未完成: {st} {job.get('error')}"); failed += 1
            continue
        r = job.get("result") or {}
        ok = (r.get("status") == "hit" and r.get("id_hex") == work_id
              and r.get("text") == WORK_TEXT)
        print(f"[e2e] 提取#{i} {el:.1f}s status={r.get('status')} id={r.get('id_hex')} "
              f"文本匹配={r.get('text') == WORK_TEXT if r.get('text') else False} "
              f"帧={r.get('frames')} 命中帧={r.get('hit_frames')} 策略={r.get('strats')}")
        if not ok:
            failed += 1

    # 4) 预热日志佐证
    logf = Path(__file__).resolve().parents[1] / "webui/data/logs/worker.log"
    if logf.exists():
        tail = logf.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
        print("[e2e] worker.log 末尾:")
        for ln in tail:
            print("   |", ln)

    # 5) 清理测试作品
    s, _ = call("DELETE", f"/api/works/{work_id}")
    print(f"[e2e] 测试作品已删除: {s}")

    if failed:
        print(f"[e2e] 失败 {failed} 项"); sys.exit(1)
    print("[e2e] 全部通过 ✓"); sys.exit(0)


if __name__ == "__main__":
    main()
