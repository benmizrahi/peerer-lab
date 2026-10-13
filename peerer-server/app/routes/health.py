"""
Health and status endpoints for Cloud Run probes and service discovery.
"""

from fastapi import APIRouter
from app.config import settings
from app.models import HealthResponse
from app.storage import get_storage

router = APIRouter(tags=["Health & Diagnostics"])


@router.get("/health", response_model=HealthResponse, summary="Service health check")
async def health_check():
    """
    Returns service health and operational metrics.
    Suitable for Cloud Run startup/liveness probes and external uptime checks.
    """
    storage = get_storage()
    stats = await storage.get_stats()
    return HealthResponse(
        status="ok",
        service="peerer-broker",
        version="1.0.0",
        uptime_seconds=stats.get("uptime_seconds", 0.0),
        storage_backend=settings.STORAGE_BACKEND,
        peers_count=stats.get("peers_count", 0),
        online_peers_count=stats.get("online_peers_count", 0),
        queued_messages_count=stats.get("queued_messages_count", 0),
    )


@router.get("/", summary="Root endpoint")
async def root():
    """
    Landing endpoint displaying service summary.
    """
    storage = get_storage()
    stats = await storage.get_stats()
    return {
        "service": "Peerer Cloud Run Broker Server",
        "description": "Centralized rendezvous and asynchronous message broker for Antigravity instances",
        "version": "1.0.0",
        "docs_url": "/docs",
        "health_url": "/health",
        "stats": stats
    }
