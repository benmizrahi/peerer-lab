#!/usr/bin/env python3
"""
Antigravity Native Peer Bridge Daemon (Sidecar)
-----------------------------------------------
An always-on listener running inside an Antigravity instance.
Supports:
1. Local HTTP listener for direct P2P connections and local CLI.
2. Cloud Run Broker synchronization (outbound-only long-polling & presence).
3. Native Sidecar Web UI dashboard embedded in Antigravity Auxiliary Pane.
"""

from collections import deque
import hmac
import json
import logging
import os
import re
import shutil
import socketserver
import ssl
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

# Add skills/peerer/scripts to sys.path to enable CLI tools import
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "skills" / "peerer" / "scripts"
if SCRIPTS_DIR.exists():
    sys.path.insert(0, str(SCRIPTS_DIR))

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


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [comm-bridge] %(message)s"
)
logger = logging.getLogger("comm-bridge")

CONVERSATION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")
START_TIME = time.time()
WEB_DIR = Path(__file__).parent / "web"

def resolve_agentapi() -> Optional[str]:
    """Resolves the path to the Antigravity agentapi binary."""
    path = shutil.which("agentapi")
    if path:
        return path
    candidate = os.path.expanduser("~/.gemini/jetski/bin/agentapi")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    candidate2 = os.path.expanduser("~/.gemini/antigravity/bin/agentapi")
    if os.path.isfile(candidate2) and os.access(candidate2, os.X_OK):
        return candidate2
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
OUTBOX: Deque[Dict[str, Any]] = deque(maxlen=50)
DISCOVERED_PEERS: Dict[str, Any] = {}
BROKER_WORKER: Optional['BrokerSyncWorker'] = None


def send_os_notification(title: str, body: str) -> None:
    """
    Fires a native desktop notification (macOS Notification Center / Linux notify-send).
    Runs in a background thread so it never blocks message dispatch.
    Disabled when config `notifications` is false, PEER_NOTIFY=0, or MOCK_AGENTAPI=1 (tests).
    """
    if os.getenv("MOCK_AGENTAPI") == "1":
        return
    if os.getenv("PEER_NOTIFY", "1") == "0" or CONFIG.get("notifications", True) is False:
        return

    def _run():
        try:
            safe_title = title.replace('"', "'")[:80]
            safe_body = body.replace('"', "'").replace("\n", " ")[:160]
            if sys.platform == "darwin":
                script = f'display notification "{safe_body}" with title "Peerer" subtitle "{safe_title}" sound name "Glass"'
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
            elif shutil.which("notify-send"):
                subprocess.run(["notify-send", f"Peerer: {safe_title}", safe_body], capture_output=True, timeout=5)
        except Exception as e:
            logger.debug("OS notification failed: %s", e)

    threading.Thread(target=_run, daemon=True).start()


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
    send_os_notification(f"{sender}: {title}", content)

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
        self.last_peers_fetch = 0.0

    def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None, timeout: int = 35) -> Optional[Any]:
        url = f"{self.broker_url}{path}"
        req_data = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "AntigravityPeerDaemon/1.0"
        }
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=get_ssl_context()) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            logger.warning("Broker HTTP %d error on %s: %s", e.code, path, err)
            return None
        except Exception as e:
            logger.warning("Broker connection error on %s: %s", path, e)
            return None

    def refresh_peers(self):
        try:
            peers_list = self._request("GET", "/api/v1/peers", timeout=10)
            if isinstance(peers_list, list):
                for p in peers_list:
                    if isinstance(p, dict) and "peer_id" in p:
                        DISCOVERED_PEERS[p["peer_id"]] = p
                self.last_peers_fetch = time.time()
        except Exception as e:
            logger.debug("Failed to refresh peers from broker: %s", e)

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
            self.refresh_peers()
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

                # Refresh peer directory every 15s
                if time.time() - self.last_peers_fetch > 15:
                    self.refresh_peers()

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

    def _send_html(self, status_code: int, html_content: str) -> None:
        raw = html_content.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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

        # Web Dashboard UI Route
        if path in ("/", "/index.html"):
            index_file = WEB_DIR / "index.html"
            if index_file.exists():
                try:
                    content = index_file.read_text(encoding="utf-8")
                    self._send_html(HTTPStatus.OK, content)
                    return
                except Exception as e:
                    logger.error("Error reading web index: %s", e)
            self._send_html(HTTPStatus.OK, "<h1>Peerer Mesh Running</h1><p>Web dashboard template missing.</p>")
            return

        # UI State API (Local UI poller)
        if path == "/api/v1/ui/state":
            broker_alive = BROKER_WORKER is not None and BROKER_WORKER.is_alive()
            peers_dict = dict(DISCOVERED_PEERS)
            try:
                import peers as peer_cli
                reg = peer_cli.load_registry()
                for name, info in reg.get("peers", {}).items():
                    if name not in peers_dict and name != CONFIG.get("instance_name"):
                        peers_dict[name] = {
                            "peer_id": name,
                            "display_name": f"{name} (Local)",
                            "capabilities": ["agentapi", "direct-p2p"],
                            "direct_url": info.get("url"),
                            "status": "online",
                            "last_seen_at": time.time()
                        }
            except Exception:
                pass

            self._send_json(HTTPStatus.OK, {
                "local_node": {
                    "instance_name": CONFIG.get("instance_name"),
                    "uptime_seconds": round(time.time() - START_TIME, 1),
                    "agentapi_ready": AGENTAPI_PATH is not None or os.getenv("MOCK_AGENTAPI") == "1",
                    "agentapi_path": AGENTAPI_PATH,
                    "host": CONFIG.get("host"),
                    "port": CONFIG.get("port"),
                    "broker_url": CONFIG.get("broker_url"),
                    "broker_connected": broker_alive,
                    "inbox_count": len(INBOX),
                    "outbox_count": len(OUTBOX)
                },
                "peers": list(peers_dict.values()),
                "inbox": list(INBOX),
                "outbox": list(OUTBOX)
            })
            return

        # Health & Status Endpoint
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

        # Inbox Endpoint (requires authentication)
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

        # Standard Ping Endpoint
        if path == "/api/v1/ping":
            self._send_json(HTTPStatus.OK, {
                "pong": True,
                "instance_name": CONFIG.get("instance_name"),
                "timestamp": time.time()
            })
            return

        # UI Task Dispatch Endpoint (Called from embedded UI)
        if path == "/api/v1/ui/send":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": f"Invalid JSON: {e}"})
                return

            peer = body.get("peer")
            message = body.get("message")
            title = body.get("title") or "Peer Task Request"
            conversation_id = body.get("conversation_id")
            model = body.get("model")
            metadata = body.get("metadata", {})

            if not peer or not message:
                self._send_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": "peer and message fields are required"})
                return

            try:
                import send as send_cli
                res = send_cli.send_message(
                    peer=peer,
                    message=message,
                    title=title,
                    conversation_id=conversation_id,
                    model=model,
                    sender=CONFIG.get("instance_name", "peerer-local"),
                    metadata=metadata
                )
            except Exception as e:
                res = {"status": "error", "error": str(e)}

            OUTBOX.append({
                "id": f"msg_out_{int(time.time() * 1000)}",
                "sent_at": time.time(),
                "target_peer": peer,
                "title": title,
                "content": message,
                "conversation_id": conversation_id,
                "model": model,
                "metadata": metadata,
                "status": res.get("status")
            })

            status_code = HTTPStatus.OK if res.get("status") == "success" else HTTPStatus.INTERNAL_SERVER_ERROR
            self._send_json(status_code, res)
            return

        # UI Ping Peer Endpoint (Called from embedded UI)
        if path == "/api/v1/ui/ping":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            target = body.get("target")
            url = body.get("url")
            try:
                import peers as peer_cli
                if url:
                    res = peer_cli.ping_url(url)
                else:
                    if target in DISCOVERED_PEERS:
                        res = {"reachable": DISCOVERED_PEERS[target].get("status") == "online", "details": DISCOVERED_PEERS[target]}
                    else:
                        res = {"reachable": False, "error": "Target peer not found"}
            except Exception as e:
                res = {"reachable": False, "error": str(e)}

            self._send_json(HTTPStatus.OK, res)
            return

        # Inbound Message Delivery Endpoint (requires authentication)
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
    global BROKER_WORKER
    host = CONFIG.get("host", "127.0.0.1")
    port = int(CONFIG.get("port", 8950))
    server_address = (host, port)
    logger.info("Starting Antigravity Peer Bridge Daemon on %s:%d", host, port)
    logger.info("Local Instance Name: %s", CONFIG.get("instance_name"))
    logger.info("agentapi path: %s", AGENTAPI_PATH or "NOT FOUND (set MOCK_AGENTAPI=1 for tests)")

    # Start Cloud Run broker worker if configured
    broker_url = CONFIG.get("broker_url")
    if broker_url:
        token = CONFIG.get("broker_token") or CONFIG.get("auth_token")
        instance = CONFIG.get("instance_name", "peerer-local")
        BROKER_WORKER = BrokerSyncWorker(broker_url, token, instance)
        BROKER_WORKER.start()

    runtime_file = Path(__file__).parent / "runtime.json"
    try:
        with open(runtime_file, "w", encoding="utf-8") as rf:
            json.dump({"host": host, "port": port, "pid": os.getpid()}, rf)
    except Exception as e:
        logger.warning("Could not write runtime.json: %s", e)

    server = ThreadedHTTPServer(server_address, PeerBridgeHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down daemon...")
    finally:
        if BROKER_WORKER:
            BROKER_WORKER.stop()
        server.server_close()
        try:
            if runtime_file.exists():
                runtime_file.unlink()
        except Exception:
            pass
        logger.info("Daemon stopped.")


if __name__ == "__main__":
    main()
