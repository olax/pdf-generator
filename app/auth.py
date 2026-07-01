"""
API-key authentication.

The service renders arbitrary URLs/HTML into PDFs — an unauthenticated instance
reachable from a network is an abuse/DoS target (and, without the host-allowlist,
an SSRF vector). Generation endpoints therefore require an API key.

Config:
- PDF_API_KEYS  — comma-separated list of valid keys (preferred), or
- PDF_API_KEY   — a single key (fallback).
If neither is set, auth is DISABLED and a loud warning is logged at startup
(keeps the local web UI / dev usage working). Production MUST set a key.

Accepted on requests via either header:
- X-API-Key: <key>
- Authorization: Bearer <key>
"""

import logging
import os
import secrets

from fastapi import Header, HTTPException

logger = logging.getLogger("pdf-service.auth")


def _load_keys() -> set[str]:
    raw = os.getenv("PDF_API_KEYS") or os.getenv("PDF_API_KEY") or ""
    return {k.strip() for k in raw.split(",") if k.strip()}


API_KEYS = _load_keys()
AUTH_ENABLED = bool(API_KEYS)

if AUTH_ENABLED:
    logger.info(f"API-key auth enabled ({len(API_KEYS)} key(s) configured)")
else:
    logger.warning(
        "API-key auth DISABLED — no PDF_API_KEYS/PDF_API_KEY set. "
        "Do NOT expose this service on an untrusted network in this state."
    )


def _key_valid(candidate: str) -> bool:
    # constant-time compare against every configured key
    return any(secrets.compare_digest(candidate, k) for k in API_KEYS)


async def require_api_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency. No-op when auth is disabled."""
    if not AUTH_ENABLED:
        return
    key = x_api_key
    if not key and authorization and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    if not key or not _key_valid(key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
