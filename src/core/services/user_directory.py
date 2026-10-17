"""Looks up users in the auth service (core never reads auth's tables directly)."""

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid

from fastapi import HTTPException, status

from src.config.settings import settings

logger = logging.getLogger("talentiq.directory")


def _fetch(access_token: str, role: str) -> list[dict]:
    url = (f"{settings.auth_service_url}/api/v1/users/directory?"
           + urllib.parse.urlencode({"role": role}))
    request = urllib.request.Request(url, headers={"Cookie": f"access_token={access_token}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


async def find_user(access_token: str, user_id: uuid.UUID, role: str) -> dict | None:
    """The active user with this id and role, or None. Uses the caller's own session cookie."""
    try:
        users = await asyncio.to_thread(_fetch, access_token, role)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.error("user directory unavailable: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Could not reach the user service. Please try again.") from exc
    return next((u for u in users if u.get("id") == str(user_id)), None)
