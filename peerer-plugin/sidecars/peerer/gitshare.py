#!/usr/bin/env python3
"""
Code travels with the session.

Capture (sender):  for the workspace behind a conversation, record origin/branch/HEAD and
                   the uncommitted work (`git diff HEAD` + small untracked files) so the
                   receiver can reproduce the sender's working tree.
Apply (receiver):  find a local checkout of the same origin (or clone it), create a
                   `peerer/<thread>` branch at the sender's HEAD if available, apply the
                   patch, copy untracked files. Never touches a dirty checkout.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PATCH_BUDGET = 700_000            # bytes of diff we are willing to ship
UNTRACKED_FILE_CAP = 200_000      # per untracked file
REPOS_ROOT = Path.home() / "Peerer Shared" / "repos"
KNOWN_REPOS_FILE = Path(__file__).parent / "known_repos.json"


def _git(repo: Path, *args: str, timeout: int = 30) -> Tuple[int, str, str]:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def normalize_origin(url: str) -> str:
    """git@github.com:a/b.git  ==  https://github.com/a/b  ==  https://github.com/a/b.git"""
    u = (url or "").strip()
    u = re.sub(r"^git@([^:]+):", r"https://\1/", u)
    u = re.sub(r"^ssh://git@", "https://", u)
    u = re.sub(r"\.git$", "", u).rstrip("/").lower()
    return u


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #
def capture(workspace: Path) -> Optional[Dict[str, Any]]:
    """Return {"repo": {...}, "patch": str, "untracked": [(relpath, bytes)], "notes": [...]} or None if not a git repo."""
    rc, root, _ = _git(workspace, "rev-parse", "--show-toplevel")
    if rc != 0:
        return None
    root_p = Path(root.strip())
    notes: List[str] = []

    _, origin, _ = _git(root_p, "config", "--get", "remote.origin.url")
    _, branch, _ = _git(root_p, "rev-parse", "--abbrev-ref", "HEAD")
    _, head, _ = _git(root_p, "rev-parse", "HEAD")
    rc_up, upstream, _ = _git(root_p, "rev-parse", "--abbrev-ref", "@{upstream}")
    ahead = 0
    if rc_up == 0:
        _, cnt, _ = _git(root_p, "rev-list", "--count", f"{upstream.strip()}..HEAD")
        ahead = int(cnt.strip() or 0)

    # Uncommitted work (staged + unstaged), binary-safe summary only for binaries
    _, patch, _ = _git(root_p, "diff", "HEAD", "--binary", timeout=60)
    if len(patch.encode("utf-8", "replace")) > PATCH_BUDGET:
        _, patch, _ = _git(root_p, "diff", "HEAD", timeout=60)  # drop binary blobs
        if len(patch.encode("utf-8", "replace")) > PATCH_BUDGET:
            _, stat, _ = _git(root_p, "diff", "HEAD", "--stat")
            notes.append("Diff too large to ship; only a summary is included.")
            patch = "# DIFF TOO LARGE — summary only\n" + stat
    _, stat, _ = _git(root_p, "diff", "HEAD", "--stat")

    # Small untracked (new) files — they aren't in `git diff HEAD`
    untracked: List[Tuple[str, bytes]] = []
    _, others, _ = _git(root_p, "ls-files", "--others", "--exclude-standard")
    for rel in [l for l in others.splitlines() if l.strip()]:
        p = root_p / rel
        try:
            if p.is_file() and p.stat().st_size <= UNTRACKED_FILE_CAP:
                untracked.append((rel, p.read_bytes()))
            elif p.is_file():
                notes.append(f"Untracked file skipped (too large): {rel}")
        except Exception:
            pass

    # Unpushed commits as a mailbox patch (so the receiver can `git am` them)
    commits_patch = ""
    if ahead:
        rc_fp, fp, _ = _git(root_p, "format-patch", f"{upstream.strip()}..HEAD", "--stdout", timeout=60)
        if rc_fp == 0 and len(fp.encode("utf-8", "replace")) <= PATCH_BUDGET:
            commits_patch = fp
        else:
            notes.append(f"{ahead} unpushed commit(s) not included (too large).")

    repo = {
        "root_name": root_p.name,
        "origin": origin.strip(),
        "origin_key": normalize_origin(origin),
        "branch": branch.strip(),
        "head": head.strip(),
        "upstream": upstream.strip() if rc_up == 0 else "",
        "ahead": ahead,
        "dirty_files": len([l for l in stat.splitlines() if "|" in l]),
        "untracked_files": len(untracked),
        "diffstat": stat.strip(),
        "captured_at": time.time(),
    }
    return {"repo": repo, "patch": patch, "commits_patch": commits_patch, "untracked": untracked, "notes": notes}


# --------------------------------------------------------------------------- #
# Known local checkouts (learned from conversation metadata the panel reports)
# --------------------------------------------------------------------------- #
def _load_known() -> Dict[str, str]:
    try:
        return json.loads(KNOWN_REPOS_FILE.read_text()) if KNOWN_REPOS_FILE.exists() else {}
    except Exception:
        return {}


def remember_checkout(path: str) -> None:
    p = Path(path)
    if not p.is_dir():
        return
    rc, origin, _ = _git(p, "config", "--get", "remote.origin.url")
    if rc != 0 or not origin.strip():
        return
    known = _load_known()
    key = normalize_origin(origin)
    if known.get(key) != str(p):
        known[key] = str(p)
        KNOWN_REPOS_FILE.write_text(json.dumps(known, indent=2) + "\n")


def find_checkout(origin: str) -> Optional[Path]:
    key = normalize_origin(origin)
    known = _load_known()
    for cand in [known.get(key)] + [str(REPOS_ROOT / n) for n in ([p.name for p in REPOS_ROOT.iterdir()] if REPOS_ROOT.is_dir() else [])]:
        if not cand:
            continue
        p = Path(cand)
        if p.is_dir():
            rc, o, _ = _git(p, "config", "--get", "remote.origin.url")
            if rc == 0 and normalize_origin(o) == key:
                return p
    return None


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #
def apply(session_dir: Path, thread_id: str = "", allow_clone: bool = True) -> Dict[str, Any]:
    """
    Apply the code changes shipped in a received session folder.
    Returns {"ok", "checkout", "branch", "applied", "untracked", "warnings", "error"}.
    """
    repo_file = session_dir / "repo.json"
    if not repo_file.exists():
        return {"ok": False, "error": "This session has no code changes attached."}
    repo = json.loads(repo_file.read_text())
    warnings: List[str] = []

    checkout = find_checkout(repo.get("origin", ""))
    cloned = False
    if checkout is None:
        if not allow_clone or not repo.get("origin"):
            return {"ok": False, "error": "No local checkout of this repository was found.", "origin": repo.get("origin")}
        REPOS_ROOT.mkdir(parents=True, exist_ok=True)
        dest = REPOS_ROOT / (repo.get("root_name") or "repo")
        if dest.exists():
            dest = REPOS_ROOT / f"{dest.name}-{int(time.time())}"
        p = subprocess.run(["git", "clone", repo["origin"], str(dest)], capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            return {"ok": False, "error": f"Clone failed: {p.stderr.strip()[-400:]}", "origin": repo.get("origin")}
        checkout, cloned = dest, True
        remember_checkout(str(dest))

    # Refuse to touch someone's in-progress work
    _, status, _ = _git(checkout, "status", "--porcelain")
    if status.strip() and not cloned:
        return {"ok": False, "checkout": str(checkout),
                "error": "Your checkout has uncommitted changes. Commit or stash them first, then apply again."}

    # Branch from the sender's HEAD when we have it, else from current HEAD
    branch = f"peerer/{thread_id or 'shared'}-{repo.get('head', '')[:7] or int(time.time())}"
    base = repo.get("head", "")
    if base:
        rc, _, _ = _git(checkout, "cat-file", "-e", f"{base}^{{commit}}")
        if rc != 0:
            _git(checkout, "fetch", "--all", "--quiet", timeout=120)
            rc, _, _ = _git(checkout, "cat-file", "-e", f"{base}^{{commit}}")
        if rc != 0:
            warnings.append(f"Sender's commit {base[:7]} isn't in your clone; applying on top of your current HEAD instead.")
            base = ""
    rc, _, err = _git(checkout, "checkout", "-b", branch, *( [base] if base else [] ))
    if rc != 0:
        return {"ok": False, "checkout": str(checkout), "error": f"Could not create branch: {err.strip()[-300:]}"}

    applied = {"commits": 0, "patch": False, "untracked": 0}
    commits = session_dir / "commits.patch"
    if commits.exists() and commits.stat().st_size > 0:
        rc, _, err = _git(checkout, "am", "--3way", str(commits), timeout=120)
        if rc == 0:
            applied["commits"] = int(repo.get("ahead", 0))
        else:
            _git(checkout, "am", "--abort")
            warnings.append(f"Unpushed commits could not be applied cleanly: {err.strip()[-200:]}")

    patch = session_dir / "changes.patch"
    if patch.exists() and patch.stat().st_size > 0 and not patch.read_text(errors="replace").startswith("# DIFF TOO LARGE"):
        rc, _, err = _git(checkout, "apply", "--3way", "--whitespace=nowarn", str(patch), timeout=120)
        if rc != 0:
            rc2, _, err2 = _git(checkout, "apply", "--reject", "--whitespace=nowarn", str(patch), timeout=120)
            if rc2 != 0:
                warnings.append(f"Patch did not apply cleanly; see *.rej files. {err2.strip()[-200:]}")
            else:
                warnings.append("Patch applied with rejects (*.rej) for some hunks.")
        applied["patch"] = True

    untracked_dir = session_dir / "untracked"
    if untracked_dir.is_dir():
        for f in untracked_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(untracked_dir)
                dest = checkout / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
                applied["untracked"] += 1

    return {"ok": True, "checkout": str(checkout), "branch": branch, "cloned": cloned,
            "applied": applied, "warnings": warnings, "origin": repo.get("origin")}
