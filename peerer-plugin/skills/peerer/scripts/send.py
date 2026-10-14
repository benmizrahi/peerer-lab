#!/usr/bin/env python3
"""
Antigravity Outbound Peer Sender
--------------------------------
Sends messages, code, or delegated tasks to another Antigravity instance.
Supports direct P2P delivery or Cloud Run Broker queuing.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

def get_ssl_context() -> ssl.SSLContext:
    """Returns an SSL context supporting certifi, default CA store, or insecure override."""
    if os.getenv("PEER_INSECURE_SSL") == "1":
        return ssl._create_unverified_context()
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


REGISTRY_PATH = Path(__file__).parent / "peers.json"
CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "sidecars" / "peerer" / "config.json"


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def resolve_peer_endpoint(target: str) -> Tuple[str, str, bool]:
    """
    Resolves target to (url, token, is_broker).
    If target is a direct URL -> (url, token, False)
    If target is an alias in peers.json -> (url, token, False)
    Otherwise, if broker is configured -> (broker_url, broker_token, True)
    """
    config = load_config()
    broker_url = os.getenv("PEER_BROKER_URL", config.get("broker_url", "")).rstrip("/")
    broker_token = os.getenv("PEER_BROKER_TOKEN", config.get("broker_token", ""))

    # 1. Check local peers.json registry
    if REGISTRY_PATH.exists():
        try:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                peers = data.get("peers", {})
                if target in peers:
                    p = peers[target]
                    return p.get("url", "").rstrip("/"), p.get("token", ""), False
        except Exception:
            pass

    # 2. Direct HTTP URL
    if target.startswith("http://") or target.startswith("https://"):
        return target.rstrip("/"), "", False

    # 3. Cloud Run Broker
    if broker_url:
        return broker_url, broker_token, True

    return f"http://{target.rstrip('/')}", "", False


def send_message(
    peer: str,
    message: str,
    title: Optional[str] = None,
    conversation_id: Optional[str] = None,
    model: Optional[str] = None,
    sender: str = "antigravity-peer",
    file: Optional[str] = None,
    token: Optional[str] = None,
    timeout: int = 15
) -> Dict[str, Any]:
    base_url, peer_token, is_broker = resolve_peer_endpoint(peer)
    auth_token = token or os.getenv("PEER_AUTH_TOKEN") or peer_token or "antigravity-peer-secret-token"

    metadata: Dict[str, Any] = {}
    content = message

    if file:
        file_path = Path(file).resolve()
        if not file_path.exists():
            return {"status": "error", "error": f"Attached file does not exist: {file}"}
        try:
            file_text = file_path.read_text(encoding="utf-8", errors="replace")
            metadata["attached_file"] = {
                "filename": file_path.name,
                "path": str(file_path),
                "size_bytes": file_path.stat().st_size
            }
            content += f"\n\n--- Attached File: {file_path.name} ---\n{file_text}\n--- End of File ---"
        except Exception as e:
            return {"status": "error", "error": f"Failed to read attached file: {e}"}

    payload: Dict[str, Any] = {
        "sender": sender,
        "content": content,
        "title": title or "Peer Task Request",
        "metadata": metadata
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if model:
        payload["model"] = model

    # If sending through the Cloud Run broker, specify target_peer
    if is_broker:
        payload["target_peer"] = peer
        target_url = f"{base_url}/api/v1/messages"
    else:
        target_url = f"{base_url}/api/v1/messages"

    body_data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url=target_url,
        data=body_data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_token}",
            "User-Agent": "AntigravityPeerClient/1.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=get_ssl_context()) as resp:
            resp_bytes = resp.read().decode("utf-8")
            status_code = resp.getcode()
            try:
                resp_json = json.loads(resp_bytes)
            except json.JSONDecodeError:
                resp_json = {"raw": resp_bytes}

            return {
                "status": "success",
                "http_status": status_code,
                "target_peer": peer,
                "via_broker": is_broker,
                "endpoint": target_url,
                "response": resp_json
            }
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        return {
            "status": "error",
            "http_status": e.code,
            "error": e.reason,
            "details": err_msg
        }
    except urllib.error.URLError as e:
        return {
            "status": "error",
            "error": "Connection failed",
            "details": str(e.reason)
        }


def main():
    parser = argparse.ArgumentParser(description="Send a message or task to a peer Antigravity instance.")
    parser.add_argument("--peer", required=True, help="Peer name or URL")
    parser.add_argument("--message", required=True, help="Text message or prompt to send")
    parser.add_argument("--title", help="Optional title for the message/task")
    parser.add_argument("--conversation-id", help="Target remote conversation ID")
    parser.add_argument("--model", choices=["flash_lite", "flash", "pro"], help="Requested model tier")
    parser.add_argument("--sender", default="antigravity-peer", help="Sender identifier")
    parser.add_argument("--file", help="Optional path to attach a file's content")
    parser.add_argument("--token", help="Bearer auth token override")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP request timeout in seconds")

    args = parser.parse_args()
    result = send_message(
        peer=args.peer,
        message=args.message,
        title=args.title,
        conversation_id=args.conversation_id,
        model=args.model,
        sender=args.sender,
        file=args.file,
        token=args.token,
        timeout=args.timeout
    )
    print(json.dumps(result, indent=2))
    if result.get("status") != "success":
        sys.exit(1)


if __name__ == "__main__":
    main()
