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


def _read_version() -> str:
    try:
        return json.loads((Path(__file__).resolve().parent.parent.parent / "plugin.json").read_text())["version"]
    except Exception:
        return "0.0.0"


VERSION = _read_version()

import sessions  # noqa: E402  (same directory)

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

CONFIG_FILE = Path(__file__).parent / "config.json"


def detect_profile() -> Dict[str, str]:
    """
    Best-effort detection of who this user is, for pre-filling the display name.
    Order: Antigravity Google account → git user.name/email → macOS full name → $USER.
    """
    profile: Dict[str, str] = {"name": "", "email": "", "source": ""}

    # 1. Antigravity / Gemini account
    try:
        acct_file = Path.home() / ".gemini" / "google_accounts.json"
        if acct_file.exists():
            data = json.loads(acct_file.read_text(encoding="utf-8"))
            email = data.get("active") or (data.get("old") or [None])[0]
            if email:
                profile["email"] = email
                profile["source"] = "antigravity"
    except Exception:
        pass

    # 2. git identity
    try:
        name = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True, timeout=3).stdout.strip()
        email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True, timeout=3).stdout.strip()
        if name and not profile["name"]:
            profile["name"] = name
            profile["source"] = profile["source"] or "git"
        if email and not profile["email"]:
            profile["email"] = email
    except Exception:
        pass

    # 3. macOS account full name (usually the nicest: "Ben Mizrahi")
    if sys.platform == "darwin":
        try:
            full = subprocess.run(["id", "-F"], capture_output=True, text=True, timeout=3).stdout.strip()
            if full and " " in full:  # prefer a real full name over a handle like g-benmizrahi
                profile["name"] = full
                profile["source"] = profile["source"] or "macos"
        except Exception:
            pass

    # 4. fallback
    if not profile["name"]:
        profile["name"] = profile["email"].split("@")[0] if profile["email"] else (os.getenv("USER") or "antigravity-user")
    return profile


def _slugify(value: str) -> str:
    """'Ben Mizrahi' -> 'ben-mizrahi' (safe as a broker peer_id)."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "peer"


def load_configuration() -> Dict[str, Any]:
    config_file = CONFIG_FILE
    defaults: Dict[str, Any] = {
        "instance_name": "",
        "display_name": "",
        "host": "127.0.0.1",
        "port": 8950,
        "auth_token": "antigravity-peer-secret-token",
        "inbox_limit": 50,
        "broker_url": "",
        "broker_token": "",
        "notifications": True
    }
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                defaults.update(loaded)
        except Exception as e:
            logger.warning("Could not read config.json, using defaults: %s", e)

    # Fill identity from the user's profile when not configured
    if not defaults.get("display_name") or not defaults.get("instance_name"):
        try:
            prof = detect_profile()
        except Exception:
            prof = {"name": os.getenv("USER") or "antigravity-user", "email": "", "source": ""}
        if not defaults.get("display_name"):
            defaults["display_name"] = prof["name"]
        if not defaults.get("instance_name"):
            defaults["instance_name"] = _slugify(defaults["display_name"]) or "antigravity-local"

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


def save_configuration(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `updates` into config.json (preserving unknown keys) and return the saved doc."""
    current: Dict[str, Any] = {}
    if CONFIG_FILE.exists():
        try:
            current = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(updates)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_FILE)
    return current


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
    metadata = dict(metadata or {})
    title = title or "Peer Message"
    sender_label = metadata.get("sender_name") or sender
    if metadata.get("sender_name") and metadata["sender_name"] != sender:
        sender_label = f"{metadata['sender_name']} ({sender})"

    # A shared session arrives as a zip inside metadata — unpack it to disk and keep only the summary
    shared_session: Optional[Dict[str, Any]] = None
    if metadata.get("type") == "session_share" and metadata.get("bundle_b64"):
        bundle = metadata.pop("bundle_b64")
        try:
            shared_session = sessions.unpack_bundle(bundle, metadata.get("sender_name") or sender, metadata.get("session_id") or "")
            metadata.update({
                "local_path": shared_session["local_path"],
                "session_title": shared_session["title"],
                "files": shared_session["files"],
                "skipped": shared_session["skipped"],
            })
            logger.info("Unpacked shared session from %s -> %s", sender, shared_session["local_path"])
        except Exception as e:
            logger.error("Failed to unpack shared session from %s: %s", sender, e)
            metadata["unpack_error"] = str(e)

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
    send_os_notification(f"{metadata.get('sender_name') or sender}: {title}", content)

    metadata_snippet = ""
    if metadata:
        metadata_snippet = f"\nMetadata Context:\n{json.dumps(metadata, indent=2)}\n"

    if shared_session:
        agent_prompt = sessions.agent_prompt_for_shared_session(sender_label, shared_session, metadata.get("note") or "")
    else:
        agent_prompt = (
            f"=== Incoming Message from Antigravity Peer [{sender_label}] ===\n"
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


def open_in_agent(title: str, prompt: str) -> Dict[str, Any]:
    """Start a new Antigravity conversation with the given prompt (no inbox record)."""
    if os.getenv("MOCK_AGENTAPI") == "1":
        return {"status": "accepted", "mock": True}
    if not AGENTAPI_PATH:
        return {"status": "error", "error": "agentapi executable not found. Ensure Antigravity is running."}
    try:
        res = subprocess.run([AGENTAPI_PATH, "new-conversation", f"--title={title}", prompt],
                             capture_output=True, text=True, timeout=30, check=True)
        return {"status": "accepted", "output": res.stdout.strip()}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "error": e.stderr.strip() or str(e)}
    except subprocess.TimeoutExpired:
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
            "display_name": CONFIG.get("display_name") or self.instance_name,
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


def start_broker_worker() -> None:
    """(Re)start the broker worker from the current CONFIG. Safe to call repeatedly."""
    global BROKER_WORKER
    if BROKER_WORKER is not None:
        try:
            BROKER_WORKER.stop()
        except Exception:
            pass
        BROKER_WORKER = None
    DISCOVERED_PEERS.clear()

    broker_url = CONFIG.get("broker_url")
    if not broker_url:
        logger.info("No broker configured — running in local-only mode.")
        return
    token = CONFIG.get("broker_token") or CONFIG.get("auth_token")
    BROKER_WORKER = BrokerSyncWorker(broker_url, token, CONFIG.get("instance_name", "peerer-local"))
    BROKER_WORKER.start()


def apply_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate + persist user-editable settings and hot-apply them.
    Editable: display_name, instance_name, broker_url, broker_token, notifications.
    """
    allowed = {"display_name", "instance_name", "broker_url", "broker_token", "notifications"}
    clean: Dict[str, Any] = {}
    for k, v in updates.items():
        if k not in allowed:
            continue
        if k == "notifications":
            clean[k] = bool(v)
        elif isinstance(v, str):
            clean[k] = v.strip()

    if "broker_url" in clean:
        url = clean["broker_url"].rstrip("/")
        if url and not re.match(r"^https?://[^\s/]+", url):
            raise ValueError("Server URL must start with http:// or https://")
        clean["broker_url"] = url

    if "display_name" in clean and not clean["display_name"]:
        clean["display_name"] = detect_profile()["name"]

    # Keep instance_name in sync with display_name unless explicitly provided
    if "display_name" in clean and not clean.get("instance_name"):
        clean["instance_name"] = _slugify(clean["display_name"])
    if "instance_name" in clean:
        clean["instance_name"] = _slugify(clean["instance_name"])

    # Empty token in the form means "keep the existing one"
    if clean.get("broker_token") == "" and CONFIG.get("broker_token"):
        clean.pop("broker_token")

    save_configuration(clean)

    identity_or_server_changed = any(
        k in clean and clean[k] != CONFIG.get(k)
        for k in ("instance_name", "display_name", "broker_url", "broker_token")
    )
    CONFIG.update(clean)
    if identity_or_server_changed:
        start_broker_worker()
    return clean


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

        # Local sessions the user can share (most recent first)
        if path == "/api/v1/ui/sessions":
            try:
                self._send_json(HTTPStatus.OK, {"sessions": sessions.list_sessions(30),
                                                "current": (self.path.split("?", 1)[1] if "?" in self.path else "")})
            except Exception as e:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return

        # UI Settings (read) — token is masked, never returned in full
        if path == "/api/v1/ui/settings":
            prof = detect_profile()
            tok = CONFIG.get("broker_token") or ""
            self._send_json(HTTPStatus.OK, {
                "display_name": CONFIG.get("display_name") or "",
                "instance_name": CONFIG.get("instance_name") or "",
                "broker_url": CONFIG.get("broker_url") or "",
                "broker_token_set": bool(tok),
                "broker_token_hint": (tok[:4] + "…" + tok[-4:]) if len(tok) >= 12 else ("••••" if tok else ""),
                "notifications": CONFIG.get("notifications", True) is not False,
                "broker_connected": BROKER_WORKER is not None and BROKER_WORKER.is_alive(),
                "profile": prof,
                "config_path": str(CONFIG_FILE),
                "version": VERSION
            })
            return

        # UI State API (Local UI poller)
        if path == "/api/v1/ui/state":
            broker_alive = BROKER_WORKER is not None and BROKER_WORKER.is_alive()
            peers_dict = dict(DISCOVERED_PEERS)
            try:
                import peers as peer_cli
                from urllib.parse import urlparse
                reg = peer_cli.load_registry()
                for name, info in reg.get("peers", {}).items():
                    host = (urlparse(info.get("url") or "").hostname or "").lower()
                    # A loopback entry is this very machine — never list yourself as a peer
                    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
                        continue
                    if name not in peers_dict and name != CONFIG.get("instance_name"):
                        peers_dict[name] = {
                            "peer_id": name,
                            "display_name": info.get("description") or name,
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
                    "display_name": CONFIG.get("display_name") or CONFIG.get("instance_name"),
                    "version": VERSION,
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
                "version": VERSION,
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

        # UI Settings (save + hot-apply)
        if path == "/api/v1/ui/settings":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                saved = apply_settings(body if isinstance(body, dict) else {})
            except ValueError as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": str(e)})
                return
            except Exception as e:
                logger.error("Failed to save settings: %s", e)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"status": "error", "error": str(e)})
                return
            self._send_json(HTTPStatus.OK, {
                "status": "ok",
                "saved": {k: ("•••" if k == "broker_token" else v) for k, v in saved.items()},
                "display_name": CONFIG.get("display_name"),
                "instance_name": CONFIG.get("instance_name"),
                "broker_url": CONFIG.get("broker_url"),
            })
            return

        # UI Settings — test a server connection without saving
        if path == "/api/v1/ui/settings/test":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(e)})
                return
            url = (body.get("broker_url") or "").strip().rstrip("/")
            token = (body.get("broker_token") or "").strip() or CONFIG.get("broker_token") or ""
            if not url:
                self._send_json(HTTPStatus.OK, {"ok": False, "error": "No server URL provided"})
                return
            started = time.time()
            try:
                req = urllib.request.Request(
                    f"{url}/api/v1/peers",
                    headers={"Authorization": f"Bearer {token}", "User-Agent": "AntigravityPeerDaemon/1.0"},
                )
                with urllib.request.urlopen(req, timeout=8, context=get_ssl_context()) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                peers_n = len(data) if isinstance(data, list) else len(data.get("peers", [])) if isinstance(data, dict) else 0
                self._send_json(HTTPStatus.OK, {"ok": True, "peers_online": peers_n, "latency_ms": round((time.time() - started) * 1000)})
            except urllib.error.HTTPError as e:
                msg = "Unauthorized — check the token" if e.code in (401, 403) else f"Server returned HTTP {e.code}"
                self._send_json(HTTPStatus.OK, {"ok": False, "error": msg})
            except Exception as e:
                self._send_json(HTTPStatus.OK, {"ok": False, "error": f"Could not reach server: {e}"})
            return

        # Share a local session (artifacts + transcript digest) with a peer
        if path == "/api/v1/ui/share-session":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": f"Invalid JSON: {e}"})
                return
            peer = body.get("peer"); conv_id = (body.get("conversation_id") or "").strip()
            note = (body.get("note") or "").strip()
            include_transcript = body.get("include_transcript", True) is not False
            if not peer or not conv_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": "peer and conversation_id are required"})
                return
            me = CONFIG.get("display_name") or CONFIG.get("instance_name")
            try:
                bundle = sessions.build_bundle(conv_id, me, include_transcript=include_transcript)
            except FileNotFoundError as e:
                self._send_json(HTTPStatus.NOT_FOUND, {"status": "error", "error": str(e)}); return
            except ValueError as e:
                self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"status": "error", "error": str(e)}); return
            man = bundle["manifest"]
            title = f"Shared session: {man['title']}"
            content = (f"{me} shared the session “{man['title']}” with you "
                       f"({len(man['files'])} file{'s' if len(man['files']) != 1 else ''}, {bundle['zip_bytes'] // 1024} KB)."
                       + (f"\n\nNote: {note}" if note else ""))
            metadata = {"type": "session_share", "sender_name": me, "session_id": conv_id,
                        "session_title": man["title"], "note": note, "files": man["files"],
                        "skipped": man["skipped"], "zip_bytes": bundle["zip_bytes"], "bundle_b64": bundle["bundle_b64"]}
            target_name = (DISCOVERED_PEERS.get(peer) or {}).get("display_name") or peer
            try:
                import send as send_cli
                res = send_cli.send_message(peer=peer, message=content, title=title,
                                            sender=CONFIG.get("instance_name", "peerer-local"), metadata=metadata)
            except Exception as e:
                res = {"status": "error", "error": str(e)}
            OUTBOX.append({
                "id": f"msg_out_{int(time.time() * 1000)}", "sent_at": time.time(),
                "target_peer": peer, "target_name": target_name, "title": title, "content": content,
                "metadata": {k: v for k, v in metadata.items() if k != "bundle_b64"},
                "status": res.get("status")
            })
            code = HTTPStatus.OK if res.get("status") == "success" else HTTPStatus.INTERNAL_SERVER_ERROR
            self._send_json(code, {**res, "zip_bytes": bundle["zip_bytes"], "files": man["files"], "skipped": man["skipped"]})
            return

        # Reveal a received session (or a file in it) in Finder / file manager — restricted to ~/Peerer Shared
        if path == "/api/v1/ui/open":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(e)}); return
            target = body.get("path") or ""
            if not target or not sessions.is_within_shared_root(target) or not Path(target).exists():
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Only files under ~/Peerer Shared can be opened"}); return
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", target])
                elif shutil.which("xdg-open"):
                    subprocess.Popen(["xdg-open", target])
                self._send_json(HTTPStatus.OK, {"ok": True})
            except Exception as e:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(e)})
            return

        # Open a received session in a new Antigravity conversation
        if path == "/api/v1/ui/open-agent":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": str(e)}); return
            target = body.get("path") or ""
            if not target or not sessions.is_within_shared_root(target) or not Path(target).is_dir():
                self._send_json(HTTPStatus.FORBIDDEN, {"status": "error", "error": "Not a received session folder"}); return
            try:
                man = json.loads((Path(target) / "manifest.json").read_text())
            except Exception:
                man = {}
            info = {"title": man.get("title") or Path(target).name, "local_path": target,
                    "files": man.get("files") or [{"path": p.name, "bytes": p.stat().st_size} for p in Path(target).iterdir() if p.is_file()]}
            who = body.get("sender_name") or man.get("shared_by") or "a peer"
            res = open_in_agent(f"[Shared by {who}] {info['title']}", sessions.agent_prompt_for_shared_session(who, info, body.get("note") or ""))
            self._send_json(HTTPStatus.OK if res.get("status") == "accepted" else HTTPStatus.INTERNAL_SERVER_ERROR, res)
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
            metadata = dict(body.get("metadata") or {})
            # Carry the human name so the receiving panel can show it instead of the peer_id
            metadata.setdefault("sender_name", CONFIG.get("display_name") or CONFIG.get("instance_name"))

            if not peer or not message:
                self._send_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": "peer and message fields are required"})
                return

            target_name = (DISCOVERED_PEERS.get(peer) or {}).get("display_name") or peer

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
                "target_name": target_name,
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
    host = CONFIG.get("host", "127.0.0.1")
    port = int(CONFIG.get("port", 8950))
    server_address = (host, port)
    logger.info("Starting Peerer daemon v%s on %s:%d", VERSION, host, port)
    logger.info("Identity: %s (peer_id: %s)", CONFIG.get("display_name"), CONFIG.get("instance_name"))
    logger.info("agentapi path: %s", AGENTAPI_PATH or "NOT FOUND (set MOCK_AGENTAPI=1 for tests)")

    # Start Cloud Run broker worker if configured (never fatal — the UI must come up regardless)
    try:
        start_broker_worker()
    except Exception as e:
        logger.error("Broker worker failed to start; continuing in local-only mode: %s", e)

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


def self_check() -> int:
    """Boot the HTTP server on an ephemeral port, hit /health, and exit. Used by the installer."""
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = ThreadedHTTPServer(("127.0.0.1", port), PeerBridgeHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            ui_ok = r.status == 200 and b"<html" in r.read(2048).lower()
        print(json.dumps({"ok": data.get("status") == "ok" and ui_ok, "version": data.get("version"),
                          "identity": CONFIG.get("display_name"), "peer_id": CONFIG.get("instance_name"),
                          "broker_configured": bool(CONFIG.get("broker_url")), "ui": ui_ok}))
        return 0 if (data.get("status") == "ok" and ui_ok) else 1
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(self_check())
    main()
