"""
Peerer Cloud Run Broker Server
-------------------------------
A centralized, secure rendezvous and asynchronous message broker for
Google Antigravity instances. Allows peers behind firewalls and NATs to connect
outbound, register presence, and exchange delegated tasks and messages.
"""

import asyncio
from contextlib import asynccontextmanager
import logging
import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import health, messages, peers
from app.security import SecurityHeadersMiddleware
from app.storage import get_storage

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] [peerer-broker] %(message)s"
)
logger = logging.getLogger("peerer-broker")


async def periodic_cleanup_task():
    """Background task to periodically clean up expired messages and dead peers."""
    storage = get_storage()
    while True:
        try:
            await asyncio.sleep(60)
            await storage.cleanup()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in periodic cleanup task: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup:
    logger.info("Starting Peerer Cloud Run Broker Server...")
    logger.info("Storage backend: %s", settings.STORAGE_BACKEND)
    logger.info("Max payload limit: %d bytes", settings.MAX_PAYLOAD_BYTES)
    cleanup_task = asyncio.create_task(periodic_cleanup_task())

    yield

    # Shutdown:
    logger.info("Shutting down Peerer Cloud Run Broker Server...")
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Peerer Broker Server",
    description="Centralized Cloud Run message broker & presence registry for Google Antigravity peers.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# 1. Custom Security Headers & Request Size Limits
app.add_middleware(SecurityHeadersMiddleware)

# 2. CORS Middleware
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "User-Agent"],
    )

# 3. Mount API Routers
app.include_router(health.router)
app.include_router(peers.router)
app.include_router(messages.router)


def run():
    """CLI entry point to launch the server locally."""
    port = int(os.getenv("PORT", str(settings.PORT)))
    host = os.getenv("HOST", settings.HOST)
    logger.info("Launching server on %s:%d", host, port)
    uvicorn.run("app.main:app", host=host, port=port, reload=settings.DEBUG)


if __name__ == "__main__":
    run()
