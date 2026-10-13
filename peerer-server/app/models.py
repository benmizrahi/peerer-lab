"""
Data schemas and models for the Peerer Broker Server.
Provides strict validation, sanitized types, and clear API documentation.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, constr

PEER_ID_REGEX = r"^[a-zA-Z0-9_\-\.]{1,64}$"
MESSAGE_ID_REGEX = r"^[a-zA-Z0-9_\-]{1,64}$"
CONV_ID_REGEX = r"^[a-zA-Z0-9_\-]{1,128}$"


class RegisterPeerRequest(BaseModel):
    peer_id: str = Field(
        ...,
        pattern=PEER_ID_REGEX,
        description="Unique identifier for the peer instance (e.g. 'workstation-alice' or 'macbook-dev')",
        examples=["alice-node"]
    )
    display_name: Optional[str] = Field(
        None,
        max_length=100,
        description="Human-readable alias or title for this peer",
        examples=["Alice's Laptop"]
    )
    capabilities: List[str] = Field(
        default_factory=list,
        description="List of capabilities or tags supported by this peer (e.g. ['code-review', 'unit-test'])",
        examples=[["code-review", "python", "fastapi"]]
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary client metadata such as OS, Antigravity version, etc."
    )
    direct_url: Optional[str] = Field(
        None,
        description="Optional direct HTTP endpoint URL if peer is reachable directly",
        examples=["http://192.168.1.50:8950"]
    )


class PeerInfo(BaseModel):
    peer_id: str
    display_name: str
    capabilities: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    registered_at: float
    last_seen_at: float
    status: str = Field(description="'online' if active within timeout, else 'offline'")
    direct_url: Optional[str] = None
    pending_messages_count: int = 0


class HeartbeatRequest(BaseModel):
    peer_id: str = Field(..., pattern=PEER_ID_REGEX)


class SendMessageRequest(BaseModel):
    target_peer: str = Field(
        ...,
        pattern=r"^(\*|[a-zA-Z0-9_\-\.]{1,64})$",
        description="Target peer_id, or '*' for broadcast to all active peers",
        examples=["alice-node"]
    )
    sender: str = Field(
        ...,
        pattern=PEER_ID_REGEX,
        description="Sender peer_id",
        examples=["bob-node"]
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=1_000_000,
        description="Body of message, prompt, or code to deliver to target peer"
    )
    title: Optional[str] = Field("Peer Message", max_length=200)
    conversation_id: Optional[str] = Field(
        None,
        pattern=CONV_ID_REGEX,
        description="Target Antigravity conversation ID on the remote peer, if continuing a thread"
    )
    model: Optional[str] = Field(
        None,
        pattern=r"^(flash_lite|flash|pro)$",
        description="Preferred model tier for new conversation"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata such as attached files or contextual data"
    )


class SendMessageResponse(BaseModel):
    status: str = "queued"
    message_id: str
    target_peer: str
    sender: str
    created_at: float


class BrokerMessage(BaseModel):
    id: str
    target_peer: str
    sender: str
    title: str
    content: str
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: float
    delivered_at: Optional[float] = None
    acked_at: Optional[float] = None
    status: str = "queued"  # "queued", "delivered", "acked", "expired"


class PollResponse(BaseModel):
    peer_id: str
    messages: List[BrokerMessage]
    timestamp: float


class AckRequest(BaseModel):
    peer_id: str = Field(..., pattern=PEER_ID_REGEX)
    message_ids: List[str] = Field(..., min_length=1)
    result: Optional[Dict[str, Any]] = None


class AckResponse(BaseModel):
    status: str = "success"
    acknowledged_count: int
    message_ids: List[str]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "peerer-broker"
    version: str = "1.0.0"
    uptime_seconds: float
    storage_backend: str
    peers_count: int
    online_peers_count: int
    queued_messages_count: int
