"""
Configuration settings for Peerer Broker Server.
Loads environment variables and sets defaults for Cloud Run deployment.
"""

import logging
import os
import secrets
from typing import List

logger = logging.getLogger("peerer-broker")

def _resolve_broker_token() -> str:
    token = os.getenv("BROKER_AUTH_TOKEN", "").strip()
    if token:
        return token
    
    # Check if a secret file exists (e.g., mounted via Cloud Run secret volume)
    secret_file = os.getenv("BROKER_AUTH_TOKEN_FILE", "")
    if secret_file and os.path.isfile(secret_file):
        try:
            with open(secret_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception as e:
            logger.warning("Failed to read BROKER_AUTH_TOKEN_FILE: %s", e)

    # Fallback to ephemeral secret for testing/local run
    generated = secrets.token_hex(32)
    logger.warning(
        "CRITICAL: BROKER_AUTH_TOKEN is not set! Generated ephemeral token: %s. "
        "Set BROKER_AUTH_TOKEN in environment for production Cloud Run deployment.",
        generated
    )
    return generated


class Settings:
    # Server configuration
    PORT: int = int(os.getenv("PORT", "8080"))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    # Security & Authentication
    AUTH_TOKEN: str = _resolve_broker_token()
    MAX_PAYLOAD_BYTES: int = int(os.getenv("MAX_PAYLOAD_BYTES", str(2 * 1024 * 1024)))  # 2MB limit

    # Storage backend: "memory" or "firestore"
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "memory").lower()
    FIRESTORE_PROJECT_ID: str = os.getenv("FIRESTORE_PROJECT_ID", "")
    FIRESTORE_COLLECTION_PREFIX: str = os.getenv("FIRESTORE_COLLECTION_PREFIX", "peerer_")

    # Presence & Heartbeat timing
    PEER_HEARTBEAT_TIMEOUT_SECONDS: int = int(os.getenv("PEER_HEARTBEAT_TIMEOUT_SECONDS", "60"))
    PEER_PURGE_SECONDS: int = int(os.getenv("PEER_PURGE_SECONDS", "3600"))

    # Message queue limits
    MESSAGE_TTL_SECONDS: int = int(os.getenv("MESSAGE_TTL_SECONDS", "86400"))  # 24 hours
    MAX_MESSAGES_PER_PEER: int = int(os.getenv("MAX_MESSAGES_PER_PEER", "1000"))
    MAX_POLL_TIMEOUT_SECONDS: int = int(os.getenv("MAX_POLL_TIMEOUT_SECONDS", "30"))

    # CORS
    CORS_ORIGINS: List[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "*").split(",")
        if origin.strip()
    ]


settings = Settings()
