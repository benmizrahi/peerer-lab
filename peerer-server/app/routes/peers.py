"""
Peer discovery and presence management routes.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.models import HeartbeatRequest, PeerInfo, RegisterPeerRequest
from app.security import verify_bearer_token
from app.storage import get_storage

router = APIRouter(
    prefix="/api/v1/peers",
    tags=["Peer Presence & Discovery"],
    dependencies=[Depends(verify_bearer_token)]
)


@router.post("/register", response_model=PeerInfo, summary="Register or update a peer instance")
async def register_peer(request: RegisterPeerRequest):
    """
    Registers a new peer node with the broker, announcing its presence,
    display name, and optional capabilities (e.g. 'code-review', 'unit-tests').
    """
    storage = get_storage()
    peer_info = await storage.register_peer(request)
    return peer_info


@router.post("/heartbeat", summary="Send presence heartbeat")
async def heartbeat_peer(request: HeartbeatRequest):
    """
    Refreshes the 'last_seen' timestamp for a peer to maintain its 'online' status.
    """
    storage = get_storage()
    updated = await storage.heartbeat_peer(request.peer_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Peer '{request.peer_id}' not found. Please register first via /api/v1/peers/register."
        )
    return {"status": "ok", "peer_id": request.peer_id}


@router.get("", response_model=List[PeerInfo], summary="List registered peers")
async def list_peers(
    online_only: bool = Query(False, description="Filter only to peers currently online (active within 60s)")
):
    """
    Returns the list of peers registered with the broker, including their
    online/offline status, capabilities, and pending message counts.
    """
    storage = get_storage()
    return await storage.list_peers(online_only=online_only)


@router.get("/{peer_id}", response_model=PeerInfo, summary="Get peer info")
async def get_peer(peer_id: str):
    """
    Fetches details for a specific peer by identifier.
    """
    storage = get_storage()
    peer_info = await storage.get_peer(peer_id)
    if not peer_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Peer '{peer_id}' not found."
        )
    return peer_info


@router.delete("/{peer_id}", summary="Unregister peer")
async def delete_peer(peer_id: str):
    """
    Unregisters a peer from the broker. Called by peers during graceful shutdown.
    """
    storage = get_storage()
    deleted = await storage.delete_peer(peer_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Peer '{peer_id}' not found."
        )
    return {"status": "deleted", "peer_id": peer_id}
