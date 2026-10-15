#!/usr/bin/env python3
"""
Session sharing for Peerer.

A "session" is an Antigravity conversation on disk:
    <app-data>/brain/<conversation-id>/
        ├── *.md, *.png, ...              ← artifacts the agent produced
        ├── .user_uploaded/               ← files/images the user dropped in
        └── .system_generated/logs/transcript.jsonl

Sharing = packing a *portable* copy of that (artifacts + a readable transcript
digest) into a small zip, shipping it inside a normal peer message, and
unpacking it on the other side under ~/Peerer Shared/<Sender>/<title>-<id>/.
The receiver's agent is then asked to open it, so the peer can literally
continue from where you were.

Nothing is pulled: only what the sender explicitly shares ever leaves a machine.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Broker caps a whole message at 2 MB; base64 adds 1/3. Keep the zip well under.
DEFAULT_BUNDLE_BUDGET = 1_200_000          # bytes of zip
TRANSCRIPT_SOFT_CAP = 350_000              # bytes of SESSION.md before trimming
MAX_UNPACKED_BYTES = 50 * 1024 * 1024      # refuse zip bombs
SHARED_ROOT = Path.home() / "Peerer Shared"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TAG_RE = re.compile(r"</?(USER_REQUEST|ADDITIONAL_METADATA|SYSTEM_MESSAGE)>")


# --------------------------------------------------------------------------- #
# Locating sessions
# --------------------------------------------------------------------------- #
def brain_dirs() -> List[Path]:
    """All Antigravity app-data 'brain' dirs on this machine (release + internal builds)."""
    base = Path.home() / ".gemini"
    out = []
    for name in ("antigravity", "jetski"):
        p = base / name / "brain"
        if p.is_dir():
            out.append(p)
    return out


def find_session_dir(conv_id: str) -> Optional[Path]:
    if not UUID_RE.match(conv_id or ""):
        return None
    for b in brain_dirs():
        p = b / conv_id
        if p.is_dir():
            return p
    return None


def _strip_user_tags(text: str) -> str:
    text = TAG_RE.sub("", text or "")
    # Drop the metadata block Antigravity appends to user turns
    cut = text.find("The current local time is:")
    if cut > 0:
        text = text[:cut]
    return text.strip()


def _artifact_files(session: Path) -> List[Path]:
    files: List[Path] = []
    for p in sorted(session.iterdir()):
        if p.is_file() and not p.name.endswith(".metadata.json") and not p.name.startswith("."):
            files.append(p)
    up = session / ".user_uploaded"
    if up.is_dir():
        files += [p for p in sorted(up.iterdir()) if p.is_file()]
    return files


def session_title(session: Path) -> str:
    """First user message, first line, trimmed — good enough as a title."""
    t = session / ".system_generated" / "logs" / "transcript.jsonl"
    try:
        with open(t, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"USER_INPUT"' not in line:
                    continue
                d = json.loads(line)
                if d.get("type") == "USER_INPUT":
                    first = _strip_user_tags(d.get("content", "")).splitlines()
                    first = next((l.strip() for l in first if l.strip()), "")
                    return (first[:80] + "…") if len(first) > 80 else (first or "Untitled session")
    except Exception:
        pass
    return "Untitled session"


def list_sessions(limit: int = 25) -> List[Dict[str, Any]]:
    rows = []
    for b in brain_dirs():
        for p in b.iterdir():
            if not p.is_dir() or not UUID_RE.match(p.name):
                continue
            try:
                mtime = max(
                    [p.stat().st_mtime]
                    + [q.stat().st_mtime for q in p.glob("*") if q.is_file()]
                    + [(p / ".system_generated" / "logs" / "transcript.jsonl").stat().st_mtime]
                    if (p / ".system_generated" / "logs" / "transcript.jsonl").exists() else [p.stat().st_mtime]
                )
            except Exception:
                mtime = p.stat().st_mtime
            arts = _artifact_files(p)
            rows.append({
                "id": p.name,
                "title": session_title(p),
                "updated_at": mtime,
                "artifacts": len(arts),
                "artifact_bytes": sum(a.stat().st_size for a in arts),
                "has_transcript": (p / ".system_generated" / "logs" / "transcript.jsonl").exists(),
            })
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return rows[:limit]


# --------------------------------------------------------------------------- #
# Building a bundle
# --------------------------------------------------------------------------- #
def _transcript_digest(session: Path, title: str, shared_by: str, conv_id: str) -> str:
    """Human-readable SESSION.md: user turns, assistant text, and a one-line note per tool call."""
    lines = [
        f"# {title}",
        "",
        f"- Shared by: **{shared_by}**",
        f"- Original conversation: `{conv_id}`",
        f"- Shared at: {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "> This is a portable digest of the conversation (tool outputs omitted). "
        "Artifacts the session produced are alongside this file.",
        "",
        "---",
        "",
    ]
    t = session / ".system_generated" / "logs" / "transcript.jsonl"
    if not t.exists():
        lines.append("_No transcript available._")
        return "\n".join(lines)

    with open(t, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            typ = d.get("type")
            if typ == "USER_INPUT":
                body = _strip_user_tags(d.get("content", ""))
                if body:
                    lines += ["## 🧑 User", "", body, ""]
            elif typ == "PLANNER_RESPONSE":
                body = (d.get("content") or "").strip()
                if body:
                    lines += ["## 🤖 Agent", "", body, ""]
                for call in d.get("tool_calls") or []:
                    name = call.get("name") or call.get("tool_name") or "tool"
                    args = call.get("args") or call.get("arguments") or {}
                    summary = ""
                    if isinstance(args, dict):
                        summary = args.get("toolSummary") or args.get("toolAction") or ""
                    lines.append(f"- 🔧 `{name}`{(' — ' + summary) if summary else ''}")
                if d.get("tool_calls"):
                    lines.append("")
    text = "\n".join(lines)
    if len(text.encode("utf-8")) > TRANSCRIPT_SOFT_CAP:
        head, tail = text[: TRANSCRIPT_SOFT_CAP // 2], text[-TRANSCRIPT_SOFT_CAP // 2:]
        text = head + "\n\n> … _(middle of transcript trimmed to fit)_ …\n\n" + tail
    return text


def build_bundle(conv_id: str, shared_by: str, include_transcript: bool = True,
                 budget: int = DEFAULT_BUNDLE_BUDGET) -> Dict[str, Any]:
    """
    Returns {"bundle_b64", "manifest", "zip_bytes"}; raises FileNotFoundError / ValueError.
    Files are added smallest-first until the budget is hit; the rest are listed as skipped.
    """
    session = find_session_dir(conv_id)
    if not session:
        raise FileNotFoundError(f"No local session with id {conv_id}")

    title = session_title(session)
    files = sorted(_artifact_files(session), key=lambda p: p.stat().st_size)
    included: List[Tuple[str, bytes]] = []
    skipped: List[Dict[str, Any]] = []

    if include_transcript:
        included.append(("SESSION.md", _transcript_digest(session, title, shared_by, conv_id).encode("utf-8")))

    # Pack incrementally and measure the real compressed size as we go
    def packed_size(items: List[Tuple[str, bytes]]) -> Tuple[int, bytes]:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for name, data in items:
                z.writestr(name, data)
        return buf.tell(), buf.getvalue()

    for p in files:
        rel = p.name if p.parent == session else f"uploads/{p.name}"
        data = p.read_bytes()
        trial = included + [(rel, data)]
        size, _ = packed_size(trial)
        if size > budget:
            skipped.append({"path": rel, "bytes": len(data), "reason": "over size budget"})
            continue
        included.append((rel, data))

    manifest = {
        "format": "peerer-session/1",
        "title": title,
        "conversation_id": conv_id,
        "shared_by": shared_by,
        "shared_at": time.time(),
        "files": [{"path": n, "bytes": len(b)} for n, b in included],
        "skipped": skipped,
    }
    included.append(("manifest.json", json.dumps(manifest, indent=2).encode("utf-8")))
    size, blob = packed_size(included)
    if size > budget + 50_000:
        raise ValueError(f"Session too large to share ({size} bytes compressed)")
    return {"bundle_b64": base64.b64encode(blob).decode("ascii"), "manifest": manifest, "zip_bytes": size}


# --------------------------------------------------------------------------- #
# Receiving a bundle
# --------------------------------------------------------------------------- #
def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._ -]+", "", s or "").strip().rstrip(".")
    s = re.sub(r"\s+", " ", s)
    return s[:60] or "session"


def unpack_bundle(bundle_b64: str, sender_name: str, conv_id: str) -> Dict[str, Any]:
    """Safely extract into ~/Peerer Shared/<sender>/<title>-<id8>/ and return a summary."""
    blob = base64.b64decode(bundle_b64)
    zf = zipfile.ZipFile(io.BytesIO(blob))

    total = 0
    for info in zf.infolist():
        name = info.filename
        if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
            raise ValueError(f"Unsafe path in bundle: {name}")
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise ValueError("Bundle too large when unpacked")

    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception:
        manifest = {}
    title = manifest.get("title") or "Shared session"
    dest = SHARED_ROOT / _slug(sender_name) / f"{_slug(title)} - {(conv_id or 'session')[:8]}"
    dest.mkdir(parents=True, exist_ok=True)

    for info in zf.infolist():
        if info.is_dir():
            continue
        out = dest / info.filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(zf.read(info))

    files = [{"path": f["path"], "bytes": f["bytes"]} for f in manifest.get("files", [])] or \
            [{"path": i.filename, "bytes": i.file_size} for i in zf.infolist() if not i.is_dir()]
    return {
        "local_path": str(dest),
        "title": title,
        "files": files,
        "skipped": manifest.get("skipped", []),
        "has_transcript": any(f["path"] == "SESSION.md" for f in files),
    }


def is_within_shared_root(path: str) -> bool:
    try:
        return Path(path).resolve().is_relative_to(SHARED_ROOT.resolve())  # py3.9+
    except AttributeError:  # pragma: no cover
        return str(Path(path).resolve()).startswith(str(SHARED_ROOT.resolve()) + os.sep)


def agent_prompt_for_shared_session(sender_label: str, info: Dict[str, Any], note: str) -> str:
    files = "\n".join(f"  - {f['path']} ({f['bytes']} bytes)" for f in info.get("files", [])) or "  (none)"
    return (
        f"=== Shared Antigravity session from peer [{sender_label}] ===\n"
        f"Title: {info.get('title')}\n"
        f"Location on this machine: {info.get('local_path')}\n"
        f"Files:\n{files}\n"
        + (f"Note from {sender_label}: {note}\n" if note else "")
        + "==========================================================\n"
        "Action: Open this shared session for me. Read SESSION.md (the conversation digest) and the "
        "artifacts in that folder, give me a concise summary of what the peer was working on and what "
        "they produced, then ask how I'd like to continue (review, build on it, or reply to the peer)."
    )
