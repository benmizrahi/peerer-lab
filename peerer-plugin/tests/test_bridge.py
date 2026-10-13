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

if __name__ == "__main__":
    unittest.main()
