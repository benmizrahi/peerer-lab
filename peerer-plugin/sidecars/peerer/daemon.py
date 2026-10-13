#!/usr/bin/env python3
"""
Antigravity Native Peer Bridge Daemon (Sidecar)
-----------------------------------------------
An always-on listener running inside an Antigravity instance.
Supports:
1. Local HTTP listener for direct P2P connections and local CLI.
2. Cloud Run Broker synchronization (outbound-only long-polling & presence).
"""

from collections import deque
import hmac
import json
import logging
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [comm-bridge] %(message)s"
)
logger = logging.getLogger("comm-bridge")

CONVERSATION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")
START_TIME = time.time()

def resolve_agentapi() -> Optional[str]:
    """Resolves the path to the Antigravity agentapi binary."""
    path = shutil.which("agentapi")
    if path:
        return path
    candidate = os.path.expanduser("~/.gemini/jetski/bin/agentapi")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None

def load_configuration() -> Dict[str, Any]:
    config_file = Path(__file__).parent / "config.json"
    defaults: Dict[str, Any] = {
        "instance_name": "antigravity-local",
        "host": "127.0.0.1",
        "port": 8950,
        "auth_token": "antigravity-peer-secret-token",
        "inbox_limit": 50,
        "broker_url": "",
        "broker_token": ""
    }
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                defaults.update(loaded)
        except Exception as e:
            logger.warning("Could not read config.json, using defaults: %s", e)

    # Allow environment overrides
    defaults["host"] = os.getenv("COMM_BRIDGE_HOST", defaults["host"])
    env_port = os.getenv("ANTIGRAVITY_SIDECAR_WEB_PORT") or os.getenv("COMM_BRIDGE_PORT")
    if env_port:
        try:
            defaults["port"] = int(env_port)
        except ValueError:
            logger.warning("Invalid port in environment variable: %s", env_port)

    defaults["auth_token"] = os.getenv("PEER_AUTH_TOKEN", defaults["auth_token"])
    defaults["broker_url"] = os.getenv("PEER_BROKER_URL", defaults.get("broker_url", "")).rstrip("/")
    defaults["broker_token"] = os.getenv("PEER_BROKER_TOKEN", defaults.get("broker_token", ""))
    return defaults

CONFIG = load_configuration()
AGENTAPI_PATH = resolve_agentapi()
INBOX: Deque[Dict[str, Any]] = deque(maxlen=int(CONFIG.get("inbox_limit", 50)))

def dispatch_peer_message(
    sender: str,
    content: str,
    title: Optional[str] = None,
    conversation_id: Optional[str] = None,
    model: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Core function that records message in INBOX and invokes agentapi.
    """
    metadata = metadata or {}
    title = title or "Peer Message"

    msg_record = {
        "id": f"msg_{int(time.time() * 1000)}",
        "received_at": time.time(),
        "sender": sender,
        "title": title,
        "content": content,
        "conversation_id": conversation_id,
        "metadata": metadata
    }
    INBOX.append(msg_record)

    metadata_snippet = ""
    if metadata:
        metadata_snippet = f"\nMetadata Context:\n{json.dumps(metadata, indent=2)}\n"

    agent_prompt = (
        f"=== Incoming Message from Antigravity Peer [{sender}] ===\n"
        f"Title: {title}\n"
        f"{metadata_snippet}"
        f"Message Body:\n{content}\n"
        f"==========================================================\n"
        f"Action Required: Respond to or execute the above request sent by peer instance '{sender}'."
    )

    if os.getenv("MOCK_AGENTAPI") == "1":
        logger.info("[MOCK] Delivered to agentapi: sender=%s, conv_id=%s", sender, conversation_id)
        return {
            "status": "accepted",
            "mock": True,
            "message_id": msg_record["id"],
            "target": conversation_id or "new_conversation",
            "instance_name": CONFIG.get("instance_name")
        }

    if not AGENTAPI_PATH:
        logger.error("agentapi executable not found on host!")
        return {
            "status": "error",
            "error": "agentapi executable not found. Ensure Antigravity is running."
        }

    try:
        if conversation_id:
            cmd = [
                AGENTAPI_PATH,
                "send-message",
                f"--title=[Peer: {sender}] {title}",
                conversation_id,
                agent_prompt
            ]
            mode = "send-message"
        else:
            cmd = [
                AGENTAPI_PATH,
                "new-conversation",
                f"--title=[Peer: {sender}] {title}"
            ]
            if model in ("flash_lite", "flash", "pro"):
                cmd.append(f"--model={model}")
            cmd.append(agent_prompt)
            mode = "new-conversation"

        logger.info("Executing agentapi (%s) for peer %s", mode, sender)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        return {
            "status": "accepted",
            "message_id": msg_record["id"],
            "mode": mode,
            "instance_name": CONFIG.get("instance_name"),
            "output": res.stdout.strip()
        }
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.strip() or str(e)
        logger.error("agentapi failed: %s", err_msg)
        return {"status": "error", "error": "agentapi invocation failed", "details": err_msg}
    except subprocess.TimeoutExpired:
        logger.error("agentapi command timed out after 30s")
        return {"status": "error", "error": "Timeout while invoking agentapi"}


class BrokerSyncWorker(threading.Thread):
    """
    Background worker that connects outbound to the Cloud Run broker,
    registers presence, sends periodic heartbeats, and long-polls for tasks.
    """
    def __init__(self, broker_url: str, token: str, instance_name: str):
        super().__init__(daemon=True)
        self.broker_url = broker_url.rstrip("/")
        self.token = token
        self.instance_name = instance_name
        self.running = True
        self.last_heartbeat = 0.0

    def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None, timeout: int = 35) -> Optional[Dict[str, Any]]:
        url = f"{self.broker_url}{path}"
        req_data = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "AntigravityPeerDaemon/1.0"
        }
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            logger.warning("Broker HTTP %d error on %s: %s", e.code, path, err)
            return None
        except Exception as e:
            logger.warning("Broker connection error on %s: %s", path, e)
            return None

    def register(self):
        logger.info("Registering with Cloud Run Broker: %s (peer_id: %s)", self.broker_url, self.instance_name)
        payload = {
            "peer_id": self.instance_name,
            "display_name": f"Antigravity Node ({self.instance_name})",
            "capabilities": ["task-delegation", "agentapi"],
            "metadata": {"host": CONFIG.get("host"), "port": CONFIG.get("port")}
        }
        res = self._request("POST", "/api/v1/peers/register", payload, timeout=10)
        if res:
            logger.info("Successfully registered with Cloud Run Broker!")
            self.last_heartbeat = time.time()
        else:
            logger.warning("Failed to register with broker, will retry in loop...")

    def heartbeat(self):
        self._request("POST", "/api/v1/peers/heartbeat", {"peer_id": self.instance_name}, timeout=10)
        self.last_heartbeat = time.time()

    def run(self):
        self.register()
        while self.running:
            try:
                # Send heartbeat every 30s
                if time.time() - self.last_heartbeat > 30:
                    self.heartbeat()

                # Long-poll messages from broker (25s timeout)
                poll_url = f"/api/v1/queue/poll?peer_id={self.instance_name}&timeout=25"
                res = self._request("GET", poll_url, timeout=35)
                if not res or not self.running:
                    time.sleep(2)
                    continue

                messages = res.get("messages", [])
                if messages:
                    logger.info("Received %d message(s) from Cloud Run Broker", len(messages))
                    acked_ids = []
                    for msg in messages:
                        logger.info("Dispatching broker message %s from %s: %s", msg["id"], msg["sender"], msg["title"])
                        dispatch_peer_message(
                            sender=msg["sender"],
                            content=msg["content"],
                            title=msg.get("title"),
                            conversation_id=msg.get("conversation_id"),
                            model=msg.get("model"),
                            metadata=msg.get("metadata")
                        )
                        acked_ids.append(msg["id"])

                    # Acknowledge processed messages
                    if acked_ids:
                        self._request("POST", "/api/v1/queue/ack", {
                            "peer_id": self.instance_name,
                            "message_ids": acked_ids
                        }, timeout=10)
            except Exception as e:
                logger.error("Error in BrokerSyncWorker loop: %s", e)
                time.sleep(3)

    def stop(self):
        self.running = False
        try:
            self._request("DELETE", f"/api/v1/peers/{self.instance_name}", timeout=5)
        except Exception:
            pass


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


class PeerBridgeHandler(BaseHTTPRequestHandler):
    server_version = "AntigravityPeerBridge/1.0"

    def _send_json(self, status_code: int, data: Dict[str, Any]) -> None:
        raw = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _is_authenticated(self) -> bool:
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False
        token = auth_header[7:].strip()
        expected = CONFIG.get("auth_token", "")
        return hmac.compare_digest(token, expected)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/health", "/status"):
            self._send_json(HTTPStatus.OK, {
                "status": "ok",
                "instance_name": CONFIG.get("instance_name"),
                "uptime_seconds": round(time.time() - START_TIME, 2),
                "agentapi_ready": AGENTAPI_PATH is not None or os.getenv("MOCK_AGENTAPI") == "1",
                "agentapi_path": AGENTAPI_PATH,
                "inbox_count": len(INBOX),
                "broker_configured": bool(CONFIG.get("broker_url"))
            })
            return

        if path == "/api/v1/inbox":
            if not self._is_authenticated():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
                return
            self._send_json(HTTPStatus.OK, {
                "instance_name": CONFIG.get("instance_name"),
                "messages": list(INBOX)
            })
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found", "path": path})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]

        if path == "/api/v1/ping":
            self._send_json(HTTPStatus.OK, {
                "pong": True,
                "instance_name": CONFIG.get("instance_name"),
                "timestamp": time.time()
            })
            return

        if path != "/api/v1/messages":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found", "path": path})
            return

        if not self._is_authenticated():
            logger.warning("Rejected unauthorized POST to %s from %s", path, self.client_address[0])
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Missing or invalid Bearer authentication token"})
            return

        content_length = self.headers.get("Content-Length")
        if not content_length:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing Content-Length"})
            return

        try:
            length = int(content_length)
            if length > 2 * 1024 * 1024:
                self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Payload exceeds 2MB limit"})
                return
            body_bytes = self.rfile.read(length)
            data = json.loads(body_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON payload: {e}"})
            return

        sender = data.get("sender")
        content = data.get("content")
        conversation_id = data.get("conversation_id")
        title = data.get("title") or "Peer Message"
        model = data.get("model")
        metadata = data.get("metadata", {})

        if not sender or not isinstance(sender, str):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "'sender' field is required (string)"})
            return

        if not content or not isinstance(content, str):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "'content' field is required (string)"})
            return

        if conversation_id:
            if not isinstance(conversation_id, str) or not CONVERSATION_ID_REGEX.match(conversation_id):
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "Invalid 'conversation_id' format (must only contain alphanumeric characters, underscores, and dashes)"
                })
                return

        result = dispatch_peer_message(
            sender=sender,
            content=content,
            title=title,
            conversation_id=conversation_id,
            model=model,
            metadata=metadata
        )

        status_code = HTTPStatus.ACCEPTED if result.get("status") == "accepted" else HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_json(status_code, result)


def main():
    host = CONFIG.get("host", "127.0.0.1")
    port = int(CONFIG.get("port", 8950))
    server_address = (host, port)
    logger.info("Starting Antigravity Peer Bridge Daemon on %s:%d", host, port)
    logger.info("Local Instance Name: %s", CONFIG.get("instance_name"))
    logger.info("agentapi path: %s", AGENTAPI_PATH or "NOT FOUND (set MOCK_AGENTAPI=1 for tests)")

    # Start Cloud Run broker worker if configured
    broker_url = CONFIG.get("broker_url")
    broker_worker = None
    if broker_url:
        token = CONFIG.get("broker_token") or CONFIG.get("auth_token")
        instance = CONFIG.get("instance_name", "peerer-local")
        broker_worker = BrokerSyncWorker(broker_url, token, instance)
        broker_worker.start()

    server = ThreadedHTTPServer(server_address, PeerBridgeHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down daemon...")
    finally:
        if broker_worker:
            broker_worker.stop()
        server.server_close()
        logger.info("Daemon stopped.")


if __name__ == "__main__":
    main()
