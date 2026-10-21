import uuid
from dataclasses import dataclass
from enum import Enum

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from jwt.exceptions import InvalidTokenError

from src.config.settings import settings


class Role(str, Enum):
    ADMIN = "admin"
    HIRING_MANAGER = "hiring_manager"
    RECRUITER = "recruiter"


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    role: Role


def get_current_user(access_token: str | None = Cookie(default=None)) -> CurrentUser:
    """Read the user from the HTTP-only access cookie issued by talent-iq-auth."""

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required. Please log in.",
        )

    try:
        payload = jwt.decode(
            access_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "role", "iat", "exp", "iss", "aud"]},
        )
        return CurrentUser(id=uuid.UUID(payload["sub"]), role=Role(payload["role"]))
    except (InvalidTokenError, ValueError, KeyError):
        # Tokens issued before the role claim existed also land here; the
        # frontend's refresh flow gets a new token with the role.
        raise HTTPException(  # noqa: B904 - the token error itself is not useful to callers
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token is invalid or expired. Please log in again.",
        )


def require_roles(*roles: Role):
    """Dependency that allows only the given roles, e.g. Depends(require_roles(Role.RECRUITER))."""

    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return user

    return checker
