#!/usr/bin/env python3
"""
Antigravity Peer Management Tool
--------------------------------
CLI for pinging, listing, adding, and removing peer Antigravity instances.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict

REGISTRY_PATH = Path(__file__).parent / "peers.json"

def load_registry() -> Dict[str, Any]:
    if REGISTRY_PATH.exists():
        try:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load {REGISTRY_PATH}: {e}", file=sys.stderr)
    return {"peers": {}}

def save_registry(data: Dict[str, Any]) -> None:
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def ping_url(url: str, timeout: int = 3) -> Dict[str, Any]:
    target = url.rstrip("/") + "/api/v1/ping"
    req = urllib.request.Request(
        url=target,
        headers={"User-Agent": "AntigravityPeerCLI/1.0"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"reachable": True, "details": data}
    except Exception as e:
        return {"reachable": False, "error": str(e)}

def cmd_list(args):
    reg = load_registry()
    peers = reg.get("peers", {})
    if not peers:
        print("No peers registered yet.")
        return

    print(f"{'NAME':<20} {'URL':<30} {'STATUS':<15} {'DESCRIPTION'}")
    print("-" * 80)
    for name, info in peers.items():
        url = info.get("url", "")
        desc = info.get("description", "")
        status_info = ping_url(url)
        status = "ONLINE" if status_info.get("reachable") else "OFFLINE"
        print(f"{name:<20} {url:<30} {status:<15} {desc}")

def cmd_ping(args):
    reg = load_registry()
    url = args.target
    if url in reg.get("peers", {}):
        url = reg["peers"][url].get("url", "")

    result = ping_url(url)
    print(json.dumps(result, indent=2))
    if not result.get("reachable"):
        sys.exit(1)

def cmd_add(args):
    reg = load_registry()
    if "peers" not in reg:
        reg["peers"] = {}

    reg["peers"][args.name] = {
        "url": args.url.rstrip("/"),
        "token": args.token or "antigravity-peer-secret-token",
        "description": args.desc or ""
    }
    save_registry(reg)
    print(f"Added peer '{args.name}' -> {args.url}")

def cmd_remove(args):
    reg = load_registry()
    if "peers" in reg and args.name in reg["peers"]:
        del reg["peers"][args.name]
        save_registry(reg)
        print(f"Removed peer '{args.name}'.")
    else:
        print(f"Peer '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Antigravity Peer CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List all peers and check their status")
    p_list.set_defaults(func=cmd_list)

    # ping
    p_ping = subparsers.add_parser("ping", help="Ping a peer by name or URL")
    p_ping.add_argument("target", help="Peer name or URL")
    p_ping.set_defaults(func=cmd_ping)

    # add
    p_add = subparsers.add_parser("add", help="Add a new peer")
    p_add.add_argument("name", help="Peer identifier name")
    p_add.add_argument("url", help="Base URL of the peer (e.g. http://192.168.1.50:8950)")
    p_add.add_argument("--token", help="Bearer token for the peer")
    p_add.add_argument("--desc", help="Description")
    p_add.set_defaults(func=cmd_add)

    # remove
    p_rm = subparsers.add_parser("remove", help="Remove a peer")
    p_rm.add_argument("name", help="Peer identifier name")
    p_rm.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
