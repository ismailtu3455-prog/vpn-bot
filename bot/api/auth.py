from __future__ import annotations

import random
import string
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Header, HTTPException, status
from jose import JWTError, jwt

from bot.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.api_secret, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.api_secret, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user_id(authorization: str = Header(...)) -> int:
    """Bearer token dependency — extracts and validates JWT, returns user_id."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
        )
    token = authorization[7:]
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user_id",
        )
    try:
        return int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user_id in token",
        )


async def get_current_admin_id(
    authorization: str = Header(...),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
) -> int:
    """Admin dependency — accepts either Bearer JWT or X-Admin-Key header."""
    # X-Admin-Key shortcut
    if x_admin_key and x_admin_key == settings.api_secret:
        # Return a synthetic admin ID
        return -1

    user_id = await get_current_user_id(authorization)

    # Check in config admin list
    if user_id in settings.get_admin_ids:
        return user_id

    # Check in DB admins
    from bot.database import crud
    admins = await crud.get_admins()
    if any(a.user_id == user_id for a in admins):
        return user_id

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access required",
    )


async def require_admin_key(x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")) -> str:
    """Require X-Admin-Key header matching api_secret."""
    if not x_admin_key or x_admin_key != settings.api_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing admin key",
        )
    return x_admin_key


def generate_otp() -> str:
    """Generate a 6-digit OTP code."""
    return "".join(random.choices(string.digits, k=6))
