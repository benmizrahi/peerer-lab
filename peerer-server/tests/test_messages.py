"""
Tests for message queuing, async long-polling, delivery, and acknowledgment.
"""

import asyncio
import sys
import unittest
from pathlib import Path

broker_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(broker_root))

from httpx import ASGITransport, AsyncClient
from app.config import settings
from app.main import app
from app.storage import get_storage


class TestMessagesAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        transport = ASGITransport(app=app)
        self.client = AsyncClient(transport=transport, base_url="http://testserver")
        self.headers = {"Authorization": f"Bearer {settings.AUTH_TOKEN}"}
        storage = get_storage()
        storage._peers.clear()
        storage._queues.clear()
        storage._messages.clear()

        # Pre-register Alice and Bob
        await self.client.post("/api/v1/peers/register", json={"peer_id": "alice"}, headers=self.headers)
        await self.client.post("/api/v1/peers/register", json={"peer_id": "bob"}, headers=self.headers)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_send_and_poll_message(self):
        # 1. Bob sends task to Alice
        msg_payload = {
            "target_peer": "alice",
            "sender": "bob",
            "title": "Code Review",
            "content": "Please review this auth module.",
            "metadata": {"file": "auth.py"}
        }
        send_resp = await self.client.post("/api/v1/messages", json=msg_payload, headers=self.headers)
        self.assertEqual(send_resp.status_code, 202)
        send_data = send_resp.json()
        self.assertEqual(send_data["status"], "queued")
        msg_id = send_data["message_id"]

        # 2. Check message status via /messages/{msg_id}
        status_resp = await self.client.get(f"/api/v1/messages/{msg_id}", headers=self.headers)
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.json()["status"], "queued")

        # 3. Alice polls her queue (immediate timeout=0)
        poll_resp = await self.client.get("/api/v1/queue/poll?peer_id=alice&timeout=0", headers=self.headers)
        self.assertEqual(poll_resp.status_code, 200)
        poll_data = poll_resp.json()
        self.assertEqual(len(poll_data["messages"]), 1)
        rec_msg = poll_data["messages"][0]
        self.assertEqual(rec_msg["id"], msg_id)
        self.assertEqual(rec_msg["sender"], "bob")
        self.assertEqual(rec_msg["title"], "Code Review")
        self.assertEqual(rec_msg["content"], "Please review this auth module.")
        self.assertEqual(rec_msg["status"], "delivered")

        # 4. Polling again returns empty queue
        poll_empty = await self.client.get("/api/v1/queue/poll?peer_id=alice&timeout=0", headers=self.headers)
        self.assertEqual(len(poll_empty.json()["messages"]), 0)

        # 5. Alice acknowledges message
        ack_resp = await self.client.post(
            "/api/v1/queue/ack",
            json={"peer_id": "alice", "message_ids": [msg_id]},
            headers=self.headers
        )
        self.assertEqual(ack_resp.status_code, 200)
        self.assertEqual(ack_resp.json()["acknowledged_count"], 1)

        # 6. Verify final status is 'acked'
        final_status = await self.client.get(f"/api/v1/messages/{msg_id}", headers=self.headers)
        self.assertEqual(final_status.json()["status"], "acked")

    async def test_long_polling_wakeup(self):
        """Tests that long-polling wakes up immediately in the event loop when a message arrives."""
        async def poll_task():
            return await self.client.get("/api/v1/queue/poll?peer_id=alice&timeout=5", headers=self.headers)

        # Launch poll task concurrently
        task = asyncio.create_task(poll_task())
        await asyncio.sleep(0.05)  # Yield control to allow polling to block on event

        # Bob sends message to Alice
        await self.client.post("/api/v1/messages", json={
            "target_peer": "alice",
            "sender": "bob",
            "content": "Wake up!"
        }, headers=self.headers)

        resp = await asyncio.wait_for(task, timeout=2.0)
        self.assertEqual(resp.status_code, 200)
        messages = resp.json()["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "Wake up!")

    async def test_broadcast_message(self):
        # Register a third node
        await self.client.post("/api/v1/peers/register", json={"peer_id": "charlie"}, headers=self.headers)

        # Alice broadcasts to '*'
        bcast_resp = await self.client.post("/api/v1/messages", json={
            "target_peer": "*",
            "sender": "alice",
            "content": "Broadcast notice to all peers"
        }, headers=self.headers)
        self.assertEqual(bcast_resp.status_code, 202)

        # Both Bob and Charlie should receive the message
        bob_poll = await self.client.get("/api/v1/queue/poll?peer_id=bob&timeout=0", headers=self.headers)
        self.assertEqual(len(bob_poll.json()["messages"]), 1)

        charlie_poll = await self.client.get("/api/v1/queue/poll?peer_id=charlie&timeout=0", headers=self.headers)
        self.assertEqual(len(charlie_poll.json()["messages"]), 1)

        # Alice (sender) should not receive her own broadcast
        alice_poll = await self.client.get("/api/v1/queue/poll?peer_id=alice&timeout=0", headers=self.headers)
        self.assertEqual(len(alice_poll.json()["messages"]), 0)


if __name__ == "__main__":
    unittest.main()
