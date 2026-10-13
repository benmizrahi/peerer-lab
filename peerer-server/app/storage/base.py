"""
Base abstract storage interface for Peerer Broker Server.
Defines presence tracking and message queuing operations.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from app.models import BrokerMessage, PeerInfo, RegisterPeerRequest


class StorageBackend(ABC):
    @abstractmethod
    async def register_peer(self, req: RegisterPeerRequest) -> PeerInfo:
        """Registers a new peer or updates an existing peer's presence and capabilities."""
        pass

    @abstractmethod
    async def heartbeat_peer(self, peer_id: str) -> bool:
        """Updates last_seen timestamp for peer. Returns False if peer not registered."""
        pass

    @abstractmethod
    async def get_peer(self, peer_id: str) -> Optional[PeerInfo]:
        """Fetches peer information by peer_id."""
        pass

    @abstractmethod
    async def list_peers(self, online_only: bool = False) -> List[PeerInfo]:
        """Lists registered peers. If online_only is True, filters to recently active peers."""
        pass

    @abstractmethod
    async def delete_peer(self, peer_id: str) -> bool:
        """Unregisters and removes peer presence."""
        pass

    @abstractmethod
    async def enqueue_message(self, msg: BrokerMessage) -> List[str]:
        """
        Enqueues message into target peer's queue.
        Supports '*' for broadcast to all currently active peers.
        Returns list of recipient peer_ids that received the message.
        """
        pass

    @abstractmethod
    async def poll_messages(self, peer_id: str, timeout: float, limit: int) -> List[BrokerMessage]:
        """
        Retrieves pending messages for peer_id.
        If no messages are available, waits asynchronously up to timeout seconds.
        Marks returned messages as 'delivered'.
        """
        pass

    @abstractmethod
    async def ack_messages(self, peer_id: str, message_ids: List[str]) -> int:
        """Marks specified message IDs as acknowledged and removes them from the active queue."""
        pass

    @abstractmethod
    async def get_message(self, message_id: str) -> Optional[BrokerMessage]:
        """Retrieves a message record by its message_id."""
        pass

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        """Returns storage diagnostics (peer counts, queue sizes, uptime)."""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Performs eviction of expired messages and stale offline peers."""
        pass
