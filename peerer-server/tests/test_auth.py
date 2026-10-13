"""
Tests for security authentication, constant-time Bearer validation, and unauthorized rejections.
"""

import sys
import unittest
from pathlib import Path

broker_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(broker_root))

from starlette.testclient import TestClient
from app.config import settings
from app.main import app


class TestAuthAndSecurity(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = settings.AUTH_TOKEN

    def test_missing_auth_header_rejected(self):
        resp = self.client.get("/api/v1/peers")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Missing Authorization header", resp.json()["detail"])

    def test_invalid_scheme_rejected(self):
        resp = self.client.get("/api/v1/peers", headers={"Authorization": "Basic 12345"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid Authorization scheme", resp.json()["detail"])

    def test_wrong_token_rejected(self):
        resp = self.client.get("/api/v1/peers", headers={"Authorization": "Bearer wrong-token-value"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid or unauthorized Bearer token", resp.json()["detail"])

    def test_valid_token_accepted(self):
        resp = self.client.get("/api/v1/peers", headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)


if __name__ == "__main__":
    unittest.main()
