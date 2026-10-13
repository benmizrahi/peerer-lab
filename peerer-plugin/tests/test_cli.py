#!/usr/bin/env python3
"""
CLI Integration Test for peerer CLI
"""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "sidecars" / "peerer"))

import daemon

TEST_HOST = "127.0.0.1"
TEST_PORT = 8998
TEST_TOKEN = "antigravity-peer-secret-token"

class TestPeererCLI(unittest.TestCase):
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

    def test_peerer_send_cli(self):
        peerer_bin = repo_root / "bin" / "peerer"
        cmd = [
            sys.executable,
            str(peerer_bin),
            "send",
            "--peer", f"http://{TEST_HOST}:{TEST_PORT}",
            "--message", "Automated CLI integration test message",
            "--title", "CLI Test",
            "--token", TEST_TOKEN,
            "--sender", "cli-tester"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = json.loads(res.stdout)
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["response"]["status"], "accepted")

    def test_peerer_ping_cli(self):
        peerer_bin = repo_root / "bin" / "peerer"
        cmd = [
            sys.executable,
            str(peerer_bin),
            "ping",
            f"http://{TEST_HOST}:{TEST_PORT}"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = json.loads(res.stdout)
        self.assertTrue(out["reachable"])
        self.assertTrue(out["details"]["pong"])

if __name__ == "__main__":
    unittest.main()
