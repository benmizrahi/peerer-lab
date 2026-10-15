#!/usr/bin/env python3
"""
Test Suite for Peerer Bridge Daemon & API
"""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "sidecars" / "peerer"))
sys.path.insert(0, str(repo_root / "skills" / "peerer" / "scripts"))

import daemon

TEST_HOST = "127.0.0.1"
TEST_PORT = 8999
TEST_TOKEN = "test-secret-token"

class TestPeererBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        daemon.CONFIG["host"] = TEST_HOST
        daemon.CONFIG["port"] = TEST_PORT
        daemon.CONFIG["auth_token"] = TEST_TOKEN
        daemon.CONFIG["broker_url"] = ""      # never touch a real broker from tests
        daemon.CONFIG["broker_token"] = ""
        os.environ["MOCK_AGENTAPI"] = "1"
        daemon.INBOX.clear()

        cls.server = daemon.ThreadedHTTPServer((TEST_HOST, TEST_PORT), daemon.PeerBridgeHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _request(self, method: str, path: str, data: dict = None, token: str = None):
        url = f"http://{TEST_HOST}:{TEST_PORT}{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req_data = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
                return resp.getcode(), json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = body
            return e.code, parsed

    def test_health_check(self):
        code, body = self._request("GET", "/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["agentapi_ready"])
        self.assertRegex(body["version"], r"^\d+\.\d+\.\d+$")

    def test_version_matches_plugin_json(self):
        manifest = json.loads((repo_root / "plugin.json").read_text())
        self.assertEqual(daemon.VERSION, manifest["version"])

    def test_ping(self):
        code, body = self._request("POST", "/api/v1/ping")
        self.assertEqual(code, 200)
        self.assertTrue(body["pong"])

    def test_unauthorized_message(self):
        code, body = self._request("POST", "/api/v1/messages", data={"content": "hello"})
        self.assertEqual(code, 401)

    def test_invalid_bearer_token(self):
        code, body = self._request("POST", "/api/v1/messages", data={"content": "hello"}, token="wrong-token")
        self.assertEqual(code, 401)

    def test_missing_fields(self):
        code, body = self._request("POST", "/api/v1/messages", data={"content": "hello"}, token=TEST_TOKEN)
        self.assertEqual(code, 400)
        code, body = self._request("POST", "/api/v1/messages", data={"sender": "peer1"}, token=TEST_TOKEN)
        self.assertEqual(code, 400)

    def test_invalid_conversation_id(self):
        payload = {
            "sender": "peer1",
            "content": "check this out",
            "conversation_id": "malicious/path; injection"
        }
        code, body = self._request("POST", "/api/v1/messages", data=payload, token=TEST_TOKEN)
        self.assertEqual(code, 400)

    def test_send_message_new_conversation(self):
        payload = {
            "sender": "peer-alice",
            "content": "Please review this PR",
            "title": "PR Review"
        }
        code, body = self._request("POST", "/api/v1/messages", data=payload, token=TEST_TOKEN)
        self.assertEqual(code, 202)
        self.assertEqual(body["status"], "accepted")
        self.assertTrue(body["mock"])

        code, inbox_body = self._request("GET", "/api/v1/inbox", token=TEST_TOKEN)
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(inbox_body["messages"]), 1)
        latest = inbox_body["messages"][-1]
        self.assertEqual(latest["sender"], "peer-alice")
        self.assertEqual(latest["title"], "PR Review")

    def test_send_message_existing_conversation(self):
        payload = {
            "sender": "peer-bob",
            "content": "Update on the query results",
            "conversation_id": "conv-1234-abcd"
        }
        code, body = self._request("POST", "/api/v1/messages", data=payload, token=TEST_TOKEN)
        self.assertEqual(code, 202)
        self.assertEqual(body["target"], "conv-1234-abcd")

    def test_web_dashboard_html(self):
        url = f"http://{TEST_HOST}:{TEST_PORT}/"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.getcode(), 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            html = resp.read().decode("utf-8")
            self.assertIn("Peerer Mesh", html)
            self.assertIn("Connected Peers", html)

    def test_ui_state(self):
        code, body = self._request("GET", "/api/v1/ui/state")
        self.assertEqual(code, 200)
        self.assertIn("local_node", body)
        self.assertIn("peers", body)
        self.assertIn("inbox", body)
        self.assertIn("outbox", body)
        self.assertTrue(len(body["local_node"]["instance_name"]) > 0)

    def test_ui_ping(self):
        code, body = self._request("POST", "/api/v1/ui/ping", data={"target": "non-existent-peer"})
        self.assertEqual(code, 200)
        self.assertFalse(body.get("reachable"))

    def test_ui_state_excludes_self_and_loopback(self):
        """peers.json entries pointing at 127.0.0.1 are this machine — never listed as peers."""
        import peers as peer_cli
        orig = peer_cli.load_registry
        peer_cli.load_registry = lambda: {"peers": {
            "local-node": {"url": "http://127.0.0.1:8950", "description": "Local Antigravity Instance"},
            "alice": {"url": "http://10.0.0.5:8950", "description": "Alice's Laptop"},
        }}
        try:
            code, body = self._request("GET", "/api/v1/ui/state")
            ids = [p["peer_id"] for p in body["peers"]]
            self.assertNotIn("local-node", ids)
            self.assertIn("alice", ids)
            alice = next(p for p in body["peers"] if p["peer_id"] == "alice")
            self.assertEqual(alice["display_name"], "Alice's Laptop")
            self.assertNotIn(daemon.CONFIG["instance_name"], ids)
        finally:
            peer_cli.load_registry = orig

    # ---------------- Session sharing ----------------

    def _fake_session(self):
        """Create a throwaway Antigravity-style session dir and point sessions.brain_dirs at it."""
        import tempfile, sessions
        root = Path(tempfile.mkdtemp()) / "brain"
        cid = "12345678-1234-1234-1234-123456789abc"
        d = root / cid
        (d / ".system_generated" / "logs").mkdir(parents=True)
        (d / ".user_uploaded").mkdir()
        (d / "report.md").write_text("# Report\n\nHello peer.")
        (d / "report.md.metadata.json").write_text("{}")
        (d / ".user_uploaded" / "shot.png").write_bytes(b"\x89PNG fake")
        (d / "huge.bin").write_bytes(os.urandom(2_000_000))  # incompressible → must be skipped
        lines = [
            {"type": "USER_INPUT", "content": "<USER_REQUEST>\nPlease build the thing\n</USER_REQUEST>\nThe current local time is: x"},
            {"type": "PLANNER_RESPONSE", "content": "Done, see report.md", "tool_calls": [{"name": "write_to_file", "args": {"toolSummary": "Write report"}}]},
        ]
        (d / ".system_generated" / "logs" / "transcript.jsonl").write_text("\n".join(json.dumps(l) for l in lines))
        orig = sessions.brain_dirs
        sessions.brain_dirs = lambda: [root]
        return cid, d, orig

    def test_session_list_and_bundle(self):
        import sessions
        cid, d, orig = self._fake_session()
        try:
            rows = sessions.list_sessions()
            self.assertEqual(rows[0]["id"], cid)
            self.assertEqual(rows[0]["title"], "Please build the thing")
            b = sessions.build_bundle(cid, "Tester")
            names = [f["path"] for f in b["manifest"]["files"]]
            self.assertIn("SESSION.md", names)
            self.assertIn("report.md", names)
            self.assertIn("uploads/shot.png", names)
            self.assertNotIn("report.md.metadata.json", names)
            self.assertNotIn("huge.bin", names)
            self.assertEqual(b["manifest"]["skipped"][0]["path"], "huge.bin")
            self.assertLess(b["zip_bytes"], sessions.DEFAULT_BUNDLE_BUDGET)
        finally:
            sessions.brain_dirs = orig

    def test_session_share_end_to_end(self):
        """Receiving a session_share message unpacks it to disk and strips the bundle from the inbox."""
        import sessions, tempfile, shutil
        cid, d, orig = self._fake_session()
        shared_root = Path(tempfile.mkdtemp()) / "Peerer Shared"
        orig_root = sessions.SHARED_ROOT
        sessions.SHARED_ROOT = shared_root
        try:
            b = sessions.build_bundle(cid, "Alice")
            meta = {"type": "session_share", "sender_name": "Alice", "session_id": cid,
                    "note": "have a look", "bundle_b64": b["bundle_b64"]}
            code, body = self._request("POST", "/api/v1/messages", data={
                "sender": "alice", "title": "Shared session", "content": "Alice shared a session", "metadata": meta
            }, token=TEST_TOKEN)
            self.assertEqual(code, 202)
            rec = daemon.INBOX[-1]
            self.assertNotIn("bundle_b64", rec["metadata"])
            lp = Path(rec["metadata"]["local_path"])
            self.assertTrue(lp.is_dir())
            self.assertTrue(str(lp).startswith(str(shared_root)))
            self.assertTrue((lp / "SESSION.md").exists())
            self.assertTrue((lp / "report.md").exists())
            self.assertTrue((lp / "uploads" / "shot.png").exists())
            self.assertEqual(rec["metadata"]["session_title"], "Please build the thing")
            # UI endpoints honour the sandbox
            code, body = self._request("POST", "/api/v1/ui/open", data={"path": "/etc/passwd"})
            self.assertEqual(code, 403)
            code, body = self._request("POST", "/api/v1/ui/open-agent", data={"path": str(lp)})
            self.assertEqual(code, 200)  # MOCK_AGENTAPI
        finally:
            sessions.brain_dirs = orig
            sessions.SHARED_ROOT = orig_root
            shutil.rmtree(shared_root.parent, ignore_errors=True)

    def test_unpack_rejects_path_traversal(self):
        import sessions, io, zipfile, base64
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../evil.txt", "x")
        with self.assertRaises(ValueError):
            sessions.unpack_bundle(base64.b64encode(buf.getvalue()).decode(), "Mallory", "")

    # ---------------- Settings ----------------

    def test_detect_profile_returns_name(self):
        prof = daemon.detect_profile()
        self.assertIn("name", prof)
        self.assertTrue(prof["name"])

    def test_slugify(self):
        self.assertEqual(daemon._slugify("Ben Mizrahi"), "ben-mizrahi")
        self.assertEqual(daemon._slugify("  Weird!!Name__ "), "weird-name")
        self.assertEqual(daemon._slugify(""), "peer")

    def test_settings_read_masks_token(self):
        daemon.CONFIG["broker_token"] = "abcd1234efgh5678ijkl"
        code, body = self._request("GET", "/api/v1/ui/settings")
        self.assertEqual(code, 200)
        self.assertTrue(body["broker_token_set"])
        self.assertNotIn("abcd1234efgh5678ijkl", json.dumps(body))
        self.assertIn("profile", body)
        for k in ("display_name", "instance_name", "broker_url", "notifications"):
            self.assertIn(k, body)

    def test_settings_save_and_hot_apply(self):
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp()) / "config.json"
        orig = daemon.CONFIG_FILE
        daemon.CONFIG_FILE = tmp
        try:
            code, body = self._request("POST", "/api/v1/ui/settings", data={
                "display_name": "Test Person",
                "broker_url": "",
                "broker_token": "",
                "notifications": False
            })
            self.assertEqual(code, 200)
            self.assertEqual(body["status"], "ok")
            self.assertEqual(daemon.CONFIG["display_name"], "Test Person")
            self.assertEqual(daemon.CONFIG["instance_name"], "test-person")
            self.assertFalse(daemon.CONFIG["notifications"])
            saved = json.loads(tmp.read_text())
            self.assertEqual(saved["display_name"], "Test Person")
            self.assertEqual(saved["instance_name"], "test-person")
            # Broker left blank → local-only, no worker
            self.assertIsNone(daemon.BROKER_WORKER)
        finally:
            daemon.CONFIG_FILE = orig

    def test_settings_rejects_bad_url(self):
        code, body = self._request("POST", "/api/v1/ui/settings", data={"broker_url": "not-a-url"})
        self.assertEqual(code, 400)
        self.assertIn("http", body["error"])

    def test_settings_blank_token_keeps_existing(self):
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp()) / "config.json"
        orig = daemon.CONFIG_FILE
        daemon.CONFIG_FILE = tmp
        daemon.CONFIG["broker_token"] = "keep-me"
        try:
            code, _ = self._request("POST", "/api/v1/ui/settings", data={"display_name": "X", "broker_token": ""})
            self.assertEqual(code, 200)
            self.assertEqual(daemon.CONFIG["broker_token"], "keep-me")
        finally:
            daemon.CONFIG_FILE = orig

    def test_settings_test_connection_bad_server(self):
        code, body = self._request("POST", "/api/v1/ui/settings/test", data={"broker_url": "http://127.0.0.1:1"})
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"])
        self.assertIn("error", body)

if __name__ == "__main__":
    unittest.main()
