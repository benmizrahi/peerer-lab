"""
Tests for health and status endpoints.
"""

import os
import sys
import unittest
from pathlib import Path

# Add broker-server to sys.path
broker_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(broker_root))

from starlette.testclient import TestClient
from app.main import app


class TestHealthEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_root_endpoint(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("service", data)
        self.assertEqual(data["version"], "1.0.0")
        self.assertIn("stats", data)

    def test_health_check_endpoint(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "peerer-broker")
        self.assertIn("uptime_seconds", data)
        self.assertIn("online_peers_count", data)

    def test_security_headers_present(self):
        resp = self.client.get("/health")
        headers = resp.headers
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("Strict-Transport-Security", headers)
        self.assertIn("Content-Security-Policy", headers)


if __name__ == "__main__":
    unittest.main()
