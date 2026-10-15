#!/usr/bin/env python3
"""
Threads — the collaboration primitive.

A thread binds *my* conversation to *their* conversation on another machine.
Messages carrying a thread_id are routed with `agentapi send-message` into the
bound conversation instead of spawning a new one, so both people (and both
agents) keep talking inside the conversations they already have open.

    thread = {
      "id": "t_ab12cd34",
      "peer": "bob",                 # peer_id
      "peer_name": "Bob Example",
      "my_conv": "<uuid>",           # conversation on this machine (may be "" until bound)
      "their_conv": "<uuid>",        # conversation on the peer's machine (learned from reply_to)
      "title": "Shared session: …",
      "created_at": 1.0, "updated_at": 1.0,
      "last_from": "me" | "them",
      "unread": 0,
    }

Persisted to threads.json next to the daemon. Tiny, append-friendly, no locking
needed beyond a module lock (single daemon process).
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

THREADS_FILE = Path(__file__).parent / "threads.json"
THREAD_ID_RE = re.compile(r"^t_[0-9a-f]{8,16}$")
_LOCK = threading.Lock()
_THREADS: Dict[str, Dict[str, Any]] = {}
_LOADED = False


def _load() -> None:
    global _LOADED, _THREADS
    if _LOADED:
        return
    try:
        _THREADS = json.loads(THREADS_FILE.read_text(encoding="utf-8")) if THREADS_FILE.exists() else {}
    except Exception:
        _THREADS = {}
    _LOADED = True


def _save() -> None:
    tmp = THREADS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_THREADS, indent=2) + "\n", encoding="utf-8")
    tmp.replace(THREADS_FILE)


def new_id() -> str:
    return "t_" + secrets.token_hex(4)


def valid_id(tid: Optional[str]) -> bool:
    return bool(tid) and bool(THREAD_ID_RE.match(tid))


def get(tid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        _load()
        t = _THREADS.get(tid)
        return dict(t) if t else None


def all_threads() -> List[Dict[str, Any]]:
    with _LOCK:
        _load()
        return sorted((dict(t) for t in _THREADS.values()), key=lambda t: t.get("updated_at", 0), reverse=True)


def find(peer: str, my_conv: str = "", their_conv: str = "") -> Optional[Dict[str, Any]]:
    """Most recent thread with this peer bound to my_conv and/or their_conv."""
    with _LOCK:
        _load()
        best = None
        for t in _THREADS.values():
            if t.get("peer") != peer:
                continue
            if my_conv and t.get("my_conv") and t["my_conv"] != my_conv:
                continue
            if their_conv and t.get("their_conv") and t["their_conv"] != their_conv:
                continue
            if not my_conv and not their_conv:
                continue
            if best is None or t.get("updated_at", 0) > best.get("updated_at", 0):
                best = t
        return dict(best) if best else None


def upsert(tid: Optional[str], peer: str, peer_name: str = "", my_conv: str = "", their_conv: str = "",
           title: str = "", last_from: str = "", bump_unread: bool = False) -> Dict[str, Any]:
    """Create or update a thread; never overwrites a known conv id with an empty one."""
    with _LOCK:
        _load()
        now = time.time()
        if tid and tid in _THREADS:
            t = _THREADS[tid]
        else:
            tid = tid if valid_id(tid) else new_id()
            t = _THREADS.setdefault(tid, {"id": tid, "peer": peer, "created_at": now, "unread": 0,
                                           "my_conv": "", "their_conv": "", "title": "", "peer_name": ""})
        if peer:
            t["peer"] = peer
        if peer_name:
            t["peer_name"] = peer_name
        if my_conv:
            t["my_conv"] = my_conv
        if their_conv:
            t["their_conv"] = their_conv
        if title and not t.get("title"):
            t["title"] = title[:120]
        if last_from:
            t["last_from"] = last_from
        if bump_unread:
            t["unread"] = int(t.get("unread", 0)) + 1
        t["updated_at"] = now
        _save()
        return dict(t)


def mark_read(tid: str) -> None:
    with _LOCK:
        _load()
        if tid in _THREADS:
            _THREADS[tid]["unread"] = 0
            _save()


def delete(tid: str) -> bool:
    with _LOCK:
        _load()
        ok = _THREADS.pop(tid, None) is not None
        if ok:
            _save()
        return ok


def reply_instructions(t: Dict[str, Any]) -> str:
    """Appended to agent prompts so the agent can continue the thread by itself."""
    who = t.get("peer_name") or t.get("peer")
    return (
        f"\n--- Collaboration thread {t['id']} with {who} ---\n"
        f"This conversation is bound to {who}'s conversation. Anything you send on this thread lands "
        f"directly in their open conversation (and their replies land here).\n"
        f"  Reply:            peerer reply --thread {t['id']} \"your message\"\n"
        f"  Reply with files: peerer reply --thread {t['id']} --attach path/to/file \"message\"\n"
        f"Use it when the user asks you to answer, ask, or hand something to {who}."
    )
