"""
Message exchange, queuing, and long-polling routes for Peerer instances.
"""

import time
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.models import (
    AckRequest,
    AckResponse,
    BrokerMessage,
    PollResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.security import verify_bearer_token
from app.storage import get_storage

router = APIRouter(
    prefix="/api/v1",
    tags=["Messaging & Queuing"],
    dependencies=[Depends(verify_bearer_token)]
)


@router.post("/messages", response_model=SendMessageResponse, status_code=status.HTTP_202_ACCEPTED, summary="Send/queue a message or task")
async def send_message(request: SendMessageRequest):
    """
    Submits a message or task to be delivered to a target peer (or broadcast to '*').
    The broker enqueues the message in the recipient's mailbox and immediately triggers
    any active long-polling listeners.
    """
    storage = get_storage()
    now = time.time()
    msg_id = f"msg_{int(now * 1000)}_{uuid.uuid4().hex[:8]}"

    broker_msg = BrokerMessage(
        id=msg_id,
        target_peer=request.target_peer,
        sender=request.sender,
        title=request.title or "Peer Message",
        content=request.content,
        conversation_id=request.conversation_id,
        model=request.model,
        metadata=request.metadata,
        created_at=now,
        status="queued"
    )

    recipients = await storage.enqueue_message(broker_msg)
    if not recipients and request.target_peer == "*":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No online peers currently available to receive broadcast."
        )

    return SendMessageResponse(
        status="queued",
        message_id=msg_id,
        target_peer=request.target_peer,
        sender=request.sender,
        created_at=now
    )


@router.get("/queue/poll", response_model=PollResponse, summary="Poll/wait for pending messages (Long Polling)")
async def poll_queue(
    peer_id: str = Query(..., description="The recipient peer's unique identifier"),
    timeout: float = Query(20.0, ge=0.0, le=30.0, description="Long-poll wait duration in seconds (0 = immediate)"),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of messages to retrieve in one batch")
):
    """
    Retrieves pending messages for a peer.
    If no messages are currently queued, this endpoint holds the connection open
    (long polling) for up to `timeout` seconds, waking up immediately if a new message arrives.
    """
    storage = get_storage()
    messages = await storage.poll_messages(peer_id=peer_id, timeout=timeout, limit=limit)
    return PollResponse(
        peer_id=peer_id,
        messages=messages,
        timestamp=time.time()
    )


@router.post("/queue/ack", response_model=AckResponse, summary="Acknowledge message receipt")
async def acknowledge_messages(request: AckRequest):
    """
    Acknowledges receipt and processing of messages. Updates message status to 'acked'.
    """
    storage = get_storage()
    count = await storage.ack_messages(peer_id=request.peer_id, message_ids=request.message_ids)
    return AckResponse(
        status="success",
        acknowledged_count=count,
        message_ids=request.message_ids
    )


@router.get("/messages/{message_id}", response_model=BrokerMessage, summary="Check message status")
async def get_message_status(message_id: str):
    """
    Fetches the delivery status and timestamps for a specific message.
    """
    storage = get_storage()
    msg = await storage.get_message(message_id)
    if not msg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{message_id}' not found or expired."
        )
    return msg
