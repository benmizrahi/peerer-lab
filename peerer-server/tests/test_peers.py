"""
Tests for peer registration, presence discovery, heartbeat, and lifecycle.
"""

import sys
import unittest
from pathlib import Path

broker_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(broker_root))

from starlette.testclient import TestClient
from app.config import settings
from app.main import app
from app.storage import get_storage


class TestPeersAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {settings.AUTH_TOKEN}"}
        # Clear storage
        storage = get_storage()
        storage._peers.clear()
        storage._queues.clear()
        storage._messages.clear()

    def test_register_peer_success(self):
        payload = {
            "peer_id": "alice-workstation",
            "display_name": "Alice Dev Box",
            "capabilities": ["code-review", "unit-test"],
            "metadata": {"os": "darwin", "version": "1.0"},
            "direct_url": "http://10.0.0.5:8950"
        }
        resp = self.client.post("/api/v1/peers/register", json=payload, headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["peer_id"], "alice-workstation")
        self.assertEqual(data["display_name"], "Alice Dev Box")
        self.assertEqual(data["status"], "online")
        self.assertEqual(data["capabilities"], ["code-review", "unit-test"])

    def test_invalid_peer_id_format(self):
        payload = {
            "peer_id": "malicious/peer;injection",
            "display_name": "Hacker"
        }
        resp = self.client.post("/api/v1/peers/register", json=payload, headers=self.headers)
        self.assertEqual(resp.status_code, 422)  # Pydantic validation error

    def test_heartbeat(self):
        # Register first
        self.client.post("/api/v1/peers/register", json={"peer_id": "bob-node"}, headers=self.headers)
        
        # Heartbeat
        resp = self.client.post("/api/v1/peers/heartbeat", json={"peer_id": "bob-node"}, headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        # Heartbeat for non-existent peer
        resp = self.client.post("/api/v1/peers/heartbeat", json={"peer_id": "ghost-node"}, headers=self.headers)
        self.assertEqual(resp.status_code, 404)

    def test_list_and_get_peers(self):
        self.client.post("/api/v1/peers/register", json={"peer_id": "node-1"}, headers=self.headers)
        self.client.post("/api/v1/peers/register", json={"peer_id": "node-2"}, headers=self.headers)

        resp = self.client.get("/api/v1/peers", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        peers = resp.json()
        self.assertEqual(len(peers), 2)
        peer_ids = [p["peer_id"] for p in peers]
        self.assertIn("node-1", peer_ids)
        self.assertIn("node-2", peer_ids)

        # Get individual peer
        resp = self.client.get("/api/v1/peers/node-1", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["peer_id"], "node-1")

    def test_delete_peer(self):
        self.client.post("/api/v1/peers/register", json={"peer_id": "node-to-delete"}, headers=self.headers)
        
        resp = self.client.delete("/api/v1/peers/node-to-delete", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "deleted")

        # Verify not found now
        resp = self.client.get("/api/v1/peers/node-to-delete", headers=self.headers)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
