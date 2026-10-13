"""
Security middleware, authentication dependencies, and hardening utilities.
Enforces constant-time token comparison, payload size limits, and standard security headers.
"""

import hmac
import logging
from typing import Optional
from fastapi import Header, HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger("peerer-broker")

async def verify_bearer_token(
    authorization: Optional[str] = Header(None, description="Bearer token authentication header")
) -> str:
    """
    Validates Bearer token using constant-time comparison (hmac.compare_digest)
    to protect against timing analysis attacks.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization scheme. Format must be 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1].strip()
    if not hmac.compare_digest(token, settings.AUTH_TOKEN):
        logger.warning("Failed authentication attempt with invalid Bearer token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unauthorized Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Injects mandatory defensive HTTP headers on every response.
    """
    async def dispatch(self, request: Request, call_next):
        # Enforce maximum payload length check from Content-Length header
        content_length = request.headers.get("Content-Length")
        if content_length:
            try:
                length = int(content_length)
                if length > settings.MAX_PAYLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Payload size ({length} bytes) exceeds limit of {settings.MAX_PAYLOAD_BYTES} bytes"
                    )
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid Content-Length header"
                )

        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none';"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response
