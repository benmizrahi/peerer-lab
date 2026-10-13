"""
Optional Google Cloud Firestore storage backend for Peerer Broker Server.
Persists peer presence and message mailboxes across Cloud Run instances and cold starts.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models import BrokerMessage, PeerInfo, RegisterPeerRequest
from app.storage.base import StorageBackend

logger = logging.getLogger("peerer-broker")

try:
    from google.cloud import firestore  # type: ignore
    HAS_FIRESTORE = True
except ImportError:
    HAS_FIRESTORE = False


class FirestoreStorage(StorageBackend):
    def __init__(self):
        if not HAS_FIRESTORE:
            raise ImportError(
                "google-cloud-firestore is not installed. "
                "Install it with 'pip install google-cloud-firestore' to use STORAGE_BACKEND=firestore"
            )
        self.db = firestore.AsyncClient(project=settings.FIRESTORE_PROJECT_ID or None)
        self.peers_col = self.db.collection(f"{settings.FIRESTORE_COLLECTION_PREFIX}peers")
        self.messages_col = self.db.collection(f"{settings.FIRESTORE_COLLECTION_PREFIX}messages")
        self._start_time = time.time()
        logger.info("Initialized FirestoreStorage backend")

    def _is_online(self, last_seen_at: float) -> bool:
        return (time.time() - last_seen_at) <= settings.PEER_HEARTBEAT_TIMEOUT_SECONDS

    async def register_peer(self, req: RegisterPeerRequest) -> PeerInfo:
        doc_ref = self.peers_col.document(req.peer_id)
        now = time.time()
        snapshot = await doc_ref.get()

        registered_at = snapshot.to_dict().get("registered_at", now) if snapshot.exists else now
        data = {
            "peer_id": req.peer_id,
            "display_name": req.display_name or req.peer_id,
            "capabilities": req.capabilities,
            "metadata": req.metadata,
            "registered_at": registered_at,
            "last_seen_at": now,
            "direct_url": req.direct_url
        }
        await doc_ref.set(data, merge=True)
        return PeerInfo(
            peer_id=req.peer_id,
            display_name=data["display_name"],
            capabilities=data["capabilities"],
            metadata=data["metadata"],
            registered_at=registered_at,
            last_seen_at=now,
            status="online",
            direct_url=req.direct_url,
            pending_messages_count=0
        )

    async def heartbeat_peer(self, peer_id: str) -> bool:
        doc_ref = self.peers_col.document(peer_id)
        snapshot = await doc_ref.get()
        if not snapshot.exists:
            return False
        await doc_ref.update({"last_seen_at": time.time()})
        return True

    async def get_peer(self, peer_id: str) -> Optional[PeerInfo]:
        doc_ref = self.peers_col.document(peer_id)
        snapshot = await doc_ref.get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        status = "online" if self._is_online(data.get("last_seen_at", 0)) else "offline"
        return PeerInfo(
            peer_id=data["peer_id"],
            display_name=data.get("display_name", data["peer_id"]),
            capabilities=data.get("capabilities", []),
            metadata=data.get("metadata", {}),
            registered_at=data.get("registered_at", 0),
            last_seen_at=data.get("last_seen_at", 0),
            status=status,
            direct_url=data.get("direct_url"),
            pending_messages_count=0
        )

    async def list_peers(self, online_only: bool = False) -> List[PeerInfo]:
        docs = self.peers_col.stream()
        results: List[PeerInfo] = []
        async for doc in docs:
            data = doc.to_dict()
            status = "online" if self._is_online(data.get("last_seen_at", 0)) else "offline"
            if online_only and status != "online":
                continue
            results.append(PeerInfo(
                peer_id=data["peer_id"],
                display_name=data.get("display_name", data["peer_id"]),
                capabilities=data.get("capabilities", []),
                metadata=data.get("metadata", {}),
                registered_at=data.get("registered_at", 0),
                last_seen_at=data.get("last_seen_at", 0),
                status=status,
                direct_url=data.get("direct_url"),
                pending_messages_count=0
            ))
        return sorted(results, key=lambda x: x.peer_id)

    async def delete_peer(self, peer_id: str) -> bool:
        doc_ref = self.peers_col.document(peer_id)
        snapshot = await doc_ref.get()
        if not snapshot.exists:
            return False
        await doc_ref.delete()
        return True

    async def enqueue_message(self, msg: BrokerMessage) -> List[str]:
        now = time.time()
        recipients: List[str] = []

        if msg.target_peer == "*":
            online_peers = await self.list_peers(online_only=True)
            recipients = [p.peer_id for p in online_peers if p.peer_id != msg.sender]
        else:
            recipients = [msg.target_peer]

        batch = self.db.batch()
        for recipient in recipients:
            copy_msg = msg.model_copy()
            copy_msg.target_peer = recipient
            doc_ref = self.messages_col.document(copy_msg.id)
            batch.set(doc_ref, copy_msg.model_dump())
        await batch.commit()
        return recipients

    async def poll_messages(self, peer_id: str, timeout: float, limit: int) -> List[BrokerMessage]:
        timeout = max(0.0, min(float(timeout), float(settings.MAX_POLL_TIMEOUT_SECONDS)))
        limit = max(1, min(int(limit), 100))
        deadline = time.time() + timeout

        while True:
            # Query queued messages for this peer
            query = (
                self.messages_col
                .where("target_peer", "==", peer_id)
                .where("status", "==", "queued")
                .order_by("created_at")
                .limit(limit)
            )
            docs = [doc async for doc in query.stream()]
            if docs:
                batch = self.db.batch()
                results: List[BrokerMessage] = []
                now = time.time()
                for doc in docs:
                    data = doc.to_dict()
                    data["status"] = "delivered"
                    data["delivered_at"] = now
                    results.append(BrokerMessage(**data))
                    batch.update(doc.reference, {"status": "delivered", "delivered_at": now})
                await batch.commit()
                # Update peer last_seen
                await self.heartbeat_peer(peer_id)
                return results

            if time.time() >= deadline:
                break
            await asyncio.sleep(min(1.0, max(0.2, deadline - time.time())))

        return []

    async def ack_messages(self, peer_id: str, message_ids: List[str]) -> int:
        batch = self.db.batch()
        count = 0
        now = time.time()
        for mid in message_ids:
            doc_ref = self.messages_col.document(mid)
            doc = await doc_ref.get()
            if doc.exists:
                batch.update(doc_ref, {"status": "acked", "acked_at": now})
                count += 1
        if count > 0:
            await batch.commit()
        return count

    async def get_message(self, message_id: str) -> Optional[BrokerMessage]:
        doc = await self.messages_col.document(message_id).get()
        if not doc.exists:
            return None
        return BrokerMessage(**doc.to_dict())

    async def get_stats(self) -> Dict[str, Any]:
        peers = await self.list_peers()
        online_count = sum(1 for p in peers if p.status == "online")
        return {
            "uptime_seconds": round(time.time() - self._start_time, 2),
            "peers_count": len(peers),
            "online_peers_count": online_count,
            "storage_backend": "firestore"
        }

    async def cleanup(self) -> None:
        pass
