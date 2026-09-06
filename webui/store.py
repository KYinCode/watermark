# -*- coding: utf-8 -*-
"""WebUI 数据存储:码本(tools/codebook.json v2,含 v1 自动迁移)+ 原子写。"""
import json
import threading
from datetime import datetime
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import common as C

LOCK = threading.RLock()  # 可重入:edit/delete 持锁期间会调用 load()


class CodebookError(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load() -> dict:
    """读码本,统一返回 v2 结构;旧 v1 自动迁移(R1.6,主作品 ID 不变)。"""
    with LOCK:
        if not C.CODEBOOK.exists():
            cb = {"version": 2, "scheme": _SCHEME, "works": []}
            _save_unlocked(cb)
            return cb
        cb = json.loads(C.CODEBOOK.read_text(encoding="utf-8"))
        if "works" not in cb:  # v1 单作品 -> v2
            cb = {"version": 2, "scheme": _SCHEME,
                  "works": [dict(id_hex=cb["id_hex"], name="主作品",
                                 text=cb["wm_text"], note="迁移自旧码本",
                                 created_at=_now())]}
            _save_unlocked(cb)
        return cb


def _save_unlocked(cb: dict):
    tmp = C.CODEBOOK.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cb, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(C.CODEBOOK)


def _save(cb: dict):
    _save_unlocked(cb)


def works() -> list:
    return load()["works"]


def add_work(name: str, text: str, note: str = "") -> dict:
    """新建作品:文本派生 ID(规则不可变),冲突拒绝(R1.7)。"""
    if not text.strip():
        raise CodebookError("版权文本不能为空")
    if not name.strip():
        raise CodebookError("作品名称不能为空")
    id_hex = f"{C.derive_id(text):08x}"
    with LOCK:
        cb = json.loads(C.CODEBOOK.read_text(encoding="utf-8")) if C.CODEBOOK.exists() else \
            {"version": 2, "scheme": _SCHEME, "works": []}
        if "works" not in cb:
            raise CodebookError("码本格式异常,请先备份 tools/codebook.json 后手工检查")
        for w in cb["works"]:
            if w["id_hex"] == id_hex:
                raise CodebookError(f"ID 冲突:该文本派生出的 ID {id_hex} 已被作品「{w['name']}」占用,请修改文本")
            if w["text"] == text:
                raise CodebookError(f"该版权文本已存在于作品「{w['name']}」")
        work = dict(id_hex=id_hex, name=name.strip(), text=text, note=note.strip(),
                    created_at=_now())
        cb["works"].append(work)
        _save(cb)
        return work


def edit_work(id_hex: str, name: str = None, note: str = None) -> dict:
    """仅允许改名称/备注;版权文本不可改(改文本=改 ID)(R1.3)。"""
    with LOCK:
        cb = load()
        for w in cb["works"]:
            if w["id_hex"] == id_hex:
                if name is not None:
                    if not name.strip():
                        raise CodebookError("作品名称不能为空")
                    w["name"] = name.strip()
                if note is not None:
                    w["note"] = note.strip()
                _save(cb)
                return w
        raise CodebookError(f"未找到作品 {id_hex}")


def delete_work(id_hex: str) -> dict:
    with LOCK:
        cb = load()
        remain = [w for w in cb["works"] if w["id_hex"] != id_hex]
        if len(remain) == len(cb["works"]):
            raise CodebookError(f"未找到作品 {id_hex}")
        removed = [w for w in cb["works"] if w["id_hex"] == id_hex][0]
        cb["works"] = remain
        _save(cb)
        return removed


def find(id_int: int):
    for w in works():
        if int(w["id_hex"], 16) == id_int:
            return w
    return None


_SCHEME = "WAM wam_mit 32-bit fixed ID; ID=sha256(text utf-8)[:4] big-endian (派生规则不可变)"
