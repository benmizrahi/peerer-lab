"""
High-performance in-memory storage backend for Peerer Broker Server.
Provides async event-driven long polling, presence tracking, and TTL cleanup.
"""

import asyncio
from collections import deque
import logging
import time
from typing import Any, Deque, Dict, List, Optional

from app.config import settings
from app.models import BrokerMessage, PeerInfo, RegisterPeerRequest
from app.storage.base import StorageBackend

logger = logging.getLogger("peerer-broker")


class MemoryStorage(StorageBackend):
    def __init__(self):
        self._lock = asyncio.Lock()
        self._peers: Dict[str, Dict[str, Any]] = {}
        self._queues: Dict[str, Deque[BrokerMessage]] = {}
        self._messages: Dict[str, BrokerMessage] = {}
        self._notifiers: Dict[str, asyncio.Event] = {}
        self._start_time = time.time()

    def _is_online(self, last_seen_at: float) -> bool:
        return (time.time() - last_seen_at) <= settings.PEER_HEARTBEAT_TIMEOUT_SECONDS

    def _peer_dict_to_info(self, p: Dict[str, Any]) -> PeerInfo:
        queue = self._queues.get(p["peer_id"], deque())
        status = "online" if self._is_online(p["last_seen_at"]) else "offline"
        return PeerInfo(
            peer_id=p["peer_id"],
            display_name=p.get("display_name") or p["peer_id"],
            capabilities=p.get("capabilities", []),
            metadata=p.get("metadata", {}),
            registered_at=p["registered_at"],
            last_seen_at=p["last_seen_at"],
            status=status,
            direct_url=p.get("direct_url"),
            pending_messages_count=len(queue)
        )

    async def register_peer(self, req: RegisterPeerRequest) -> PeerInfo:
        async with self._lock:
            now = time.time()
            existing = self._peers.get(req.peer_id)
            registered_at = existing["registered_at"] if existing else now

            self._peers[req.peer_id] = {
                "peer_id": req.peer_id,
                "display_name": req.display_name or req.peer_id,
                "capabilities": req.capabilities,
                "metadata": req.metadata,
                "registered_at": registered_at,
                "last_seen_at": now,
                "direct_url": req.direct_url
            }

            if req.peer_id not in self._queues:
                self._queues[req.peer_id] = deque(maxlen=settings.MAX_MESSAGES_PER_PEER)
            if req.peer_id not in self._notifiers:
                self._notifiers[req.peer_id] = asyncio.Event()

            logger.info("Peer registered: %s (%s)", req.peer_id, req.display_name)
            return self._peer_dict_to_info(self._peers[req.peer_id])

    async def heartbeat_peer(self, peer_id: str) -> bool:
        async with self._lock:
            if peer_id not in self._peers:
                return False
            self._peers[peer_id]["last_seen_at"] = time.time()
            return True

    async def get_peer(self, peer_id: str) -> Optional[PeerInfo]:
        async with self._lock:
            p = self._peers.get(peer_id)
            if not p:
                return None
            return self._peer_dict_to_info(p)

    async def list_peers(self, online_only: bool = False) -> List[PeerInfo]:
        async with self._lock:
            results: List[PeerInfo] = []
            for p in self._peers.values():
                info = self._peer_dict_to_info(p)
                if online_only and info.status != "online":
                    continue
                results.append(info)
            return sorted(results, key=lambda x: x.peer_id)

    async def delete_peer(self, peer_id: str) -> bool:
        async with self._lock:
            if peer_id in self._peers:
                del self._peers[peer_id]
                self._queues.pop(peer_id, None)
                self._notifiers.pop(peer_id, None)
                logger.info("Peer deleted: %s", peer_id)
                return True
            return False

    async def enqueue_message(self, msg: BrokerMessage) -> List[str]:
        async with self._lock:
            recipients: List[str] = []
            now = time.time()

            # Broadcast mode
            if msg.target_peer == "*":
                recipients = [
                    pid for pid, p in self._peers.items()
                    if pid != msg.sender and self._is_online(p["last_seen_at"])
                ]
            else:
                recipients = [msg.target_peer]

            for recipient in recipients:
                if recipient not in self._queues:
                    self._queues[recipient] = deque(maxlen=settings.MAX_MESSAGES_PER_PEER)
                if recipient not in self._notifiers:
                    self._notifiers[recipient] = asyncio.Event()

                # Clone message for recipient if broadcast
                copy_msg = msg.model_copy()
                copy_msg.target_peer = recipient
                self._queues[recipient].append(copy_msg)
                self._messages[copy_msg.id] = copy_msg

                # Trigger event for long-polling listener
                self._notifiers[recipient].set()

            logger.info("Enqueued message %s from %s for %s", msg.id, msg.sender, recipients)
            return recipients

    async def poll_messages(self, peer_id: str, timeout: float, limit: int) -> List[BrokerMessage]:
        # Clamp timeout between 0 and MAX_POLL_TIMEOUT_SECONDS
        timeout = max(0.0, min(float(timeout), float(settings.MAX_POLL_TIMEOUT_SECONDS)))
        limit = max(1, min(int(limit), 100))

        # Check existing queue first
        messages = await self._drain_queue(peer_id, limit)
        if messages or timeout == 0:
            return messages

        # Long-polling wait for incoming message
        async with self._lock:
            if peer_id not in self._notifiers:
                self._notifiers[peer_id] = asyncio.Event()
            event = self._notifiers[peer_id]
            event.clear()

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

        return await self._drain_queue(peer_id, limit)

    async def _drain_queue(self, peer_id: str, limit: int) -> List[BrokerMessage]:
        async with self._lock:
            # Also count polling as heartbeat
            if peer_id in self._peers:
                self._peers[peer_id]["last_seen_at"] = time.time()

            queue = self._queues.get(peer_id)
            if not queue:
                return []

            batch: List[BrokerMessage] = []
            while queue and len(batch) < limit:
                msg = queue.popleft()
                msg.delivered_at = time.time()
                msg.status = "delivered"
                self._messages[msg.id] = msg
                batch.append(msg)

            return batch

    async def ack_messages(self, peer_id: str, message_ids: List[str]) -> int:
        async with self._lock:
            count = 0
            now = time.time()
            for mid in message_ids:
                if mid in self._messages:
                    msg = self._messages[mid]
                    msg.acked_at = now
                    msg.status = "acked"
                    count += 1
            return count

    async def get_message(self, message_id: str) -> Optional[BrokerMessage]:
        async with self._lock:
            return self._messages.get(message_id)

    async def get_stats(self) -> Dict[str, Any]:
        async with self._lock:
            now = time.time()
            online_count = sum(1 for p in self._peers.values() if self._is_online(p["last_seen_at"]))
            queued_count = sum(len(q) for q in self._queues.values())
            return {
                "uptime_seconds": round(now - self._start_time, 2),
                "peers_count": len(self._peers),
                "online_peers_count": online_count,
                "queued_messages_count": queued_count,
                "total_messages_tracked": len(self._messages)
            }

    async def cleanup(self) -> None:
        async with self._lock:
            now = time.time()
            # Purge expired messages from global index
            expired_ids = [
                mid for mid, msg in self._messages.items()
                if (now - msg.created_at) > settings.MESSAGE_TTL_SECONDS
            ]
            for mid in expired_ids:
                del self._messages[mid]

            # Purge offline peers not seen for PEER_PURGE_SECONDS
            stale_peers = [
                pid for pid, p in self._peers.items()
                if (now - p["last_seen_at"]) > settings.PEER_PURGE_SECONDS
            ]
            for pid in stale_peers:
                del self._peers[pid]
                self._queues.pop(pid, None)
                self._notifiers.pop(pid, None)

            if expired_ids or stale_peers:
                logger.info(
                    "Cleanup completed: purged %d expired messages, %d stale peers",
                    len(expired_ids), len(stale_peers)
                )
