from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from bot.api.auth import (
    create_access_token, generate_otp, get_current_user_id,
    ACCESS_TOKEN_EXPIRE_HOURS,
)
from bot.config import settings, db_settings
from bot.database import crud
from bot.services import vpn as vpn_service

logger = logging.getLogger(__name__)
router = APIRouter()

# Bot instance injected at startup
_bot = None


def set_bot(bot) -> None:
    global _bot
    _bot = bot


# ─── Schemas ──────────────────────────────────────────────────────────────────

class OtpRequestBody(BaseModel):
    user_id: int


class OtpVerifyBody(BaseModel):
    user_id: int
    code: str


# ─── Auth endpoints ───────────────────────────────────────────────────────────

@router.post("/auth/request-otp")
async def request_otp(body: OtpRequestBody):
    """Send OTP code to user's Telegram."""
    user = await crud.get_user(body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Rate limit: check if OTP created in last 60 seconds
    existing = await crud.get_latest_otp(body.user_id)
    if existing:
        from datetime import datetime
        elapsed = (existing.expires_at - existing.created_at).total_seconds()
        remaining = elapsed - (datetime.utcnow() - existing.created_at).total_seconds()
        # If created less than 60 seconds ago
        import time
        created_diff = (datetime.utcnow() - existing.created_at).total_seconds()
        if created_diff < 60:
            raise HTTPException(
                status_code=429,
                detail=f"OTP already sent. Please wait {int(60 - created_diff)} seconds.",
            )

    code = generate_otp()
    await crud.create_otp(body.user_id, code, expires_in_seconds=300)

    if _bot:
        try:
            from bot.texts import Texts
            await _bot.send_message(
                body.user_id,
                Texts.OTP_MESSAGE.format(code=code),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"OTP send error for {body.user_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to send OTP via Telegram")

    return {"ok": True, "message": "Code sent to your Telegram"}


@router.post("/auth/verify-otp")
async def verify_otp(body: OtpVerifyBody):
    """Verify OTP and return JWT session token."""
    user = await crud.get_user(body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    valid = await crud.verify_otp(body.user_id, body.code)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP code")

    token = create_access_token(
        data={"sub": str(body.user_id)},
        expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    )
    await crud.create_web_session(body.user_id, token, expires_in_hours=ACCESS_TOKEN_EXPIRE_HOURS)

    return {
        "ok": True,
        "token": token,
        "user_id": body.user_id,
        "expires_in": ACCESS_TOKEN_EXPIRE_HOURS * 3600,
    }


class EmailRequestBody(BaseModel):
    email: str
    ref: str | None = None

class EmailVerifyBody(BaseModel):
    email: str
    code: str

@router.post("/auth/email/request")
async def request_email_code(body: EmailRequestBody):
    email = body.email.strip().lower()
    user = await crud.get_user_by_email(email)
    if not user:
        import random
        # Создаем нового пользователя с виртуальным ID (10-12 цифр, чтобы не конфликтовать с Telegram)
        new_user_id = random.randint(10000000000, 99999999999)
        username = email.split('@')[0]
        
        ref_id = None
        if body.ref:
            # ref can be B<id> or P<id> or just <id>
            try:
                # Remove B or P prefix if it exists
                clean_ref = body.ref[1:] if body.ref.startswith(('B', 'P', 'b', 'p')) else body.ref
                ref_id = int(clean_ref)
            except ValueError:
                pass

        await crud.register_user(
            user_id=new_user_id,
            username=username,
            first_name="Web",
            last_name="User",
            ref_id=ref_id
        )
        await crud.set_user_email(new_user_id, email)
        user = await crud.get_user(new_user_id)
        
    code = generate_otp()
    await crud.create_email_verification(email, code, user.user_id, expires_in_seconds=300)
    
    from bot.services.email import send_verification_email
    success = await send_verification_email(email, code)
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка отправки письма")
        
    return {"ok": True, "message": "Code sent to email"}


@router.post("/auth/email/verify")
async def verify_email_code_route(body: EmailVerifyBody):
    email = body.email.strip().lower()
    code = body.code.strip()
    
    user_id = await crud.verify_email_code(email, code)
    if not user_id:
        raise HTTPException(status_code=401, detail="Неверный или просроченный код")
        
    token = create_access_token(
        data={"sub": str(user_id)},
        expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    )
    await crud.create_web_session(user_id, token, expires_in_hours=ACCESS_TOKEN_EXPIRE_HOURS)
    
    return {"ok": True, "token": token, "user_id": user_id}

import uuid

@router.get("/auth/session")
async def create_auth_session_endpoint():
    """Create a new Deep Link auth session."""
    session_id = str(uuid.uuid4())[:8]
    await crud.create_auth_session(session_id)
    return {"ok": True, "session_id": session_id}

@router.get("/auth/status")
async def check_session_status(session_id: str):
    """Check if session is authenticated and return JWT."""
    user_id = await crud.get_authenticated_session_user(session_id)
    if not user_id:
        return {"ok": False, "status": "pending"}

    # Authenticated! Generate token
    token = create_access_token(
        data={"sub": str(user_id)},
        expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    )
    await crud.create_web_session(user_id, token, expires_in_hours=ACCESS_TOKEN_EXPIRE_HOURS)

    return {
        "ok": True,
        "status": "authenticated",
        "token": token,
        "user_id": user_id,
        "expires_in": ACCESS_TOKEN_EXPIRE_HOURS * 3600,
    }


# ─── User endpoints ───────────────────────────────────────────────────────────

@router.get("/user/me")
async def get_me(user_id: int = Depends(get_current_user_id)):
    user = await crud.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    refs_count = await crud.get_referrals_count(user_id)
    paid_count = await crud.get_paid_invoices_count(user_id)
    return {
        "user_id": user.user_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "balance": user.balance,
        "ref_earned": user.ref_earned,
        "bonus_days_stat": user.bonus_days_stat,
        "referrals_count": refs_count,
        "paid_invoices": paid_count,
        "vpn_name": user.vpn_name,
        "email": user.email,
        "test_taken": user.test_taken,
        "free_reissue_used": user.free_reissue_used,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("/user/vpn")
async def get_vpn_info(user_id: int = Depends(get_current_user_id)):
    """Get user's VPN subscription details from H1Cloud API."""
    user = await crud.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.vpn_name:
        raise HTTPException(status_code=404, detail="No VPN subscription found")

    try:
        data = await vpn_service.get_client(user.vpn_name)
        client = vpn_service.normalize_client_payload(data)
    except vpn_service.VPNAPIError as e:
        raise HTTPException(status_code=503, detail=f"VPN API error: {e}")

    sub_url = f"https://{settings.subscription_domain}/sub/{user.sub_token}"
    return {
        "ok": True,
        "data": {
            "vpn_name": user.vpn_name,
            "days_left": client.get("left_days", 0),
            "expires_at": client.get("expires_at"),
            "subscription_url": sub_url,
            "used_bytes": client.get("used_traffic_bytes") or 0,
            "limit_bytes": client.get("traffic_limit_bytes") or 0,
            "is_banned": client.get("is_banned", False),
            "links": client.get("links") or [],
        }
    }


@router.get("/user/invoices")
async def get_invoices(
    user_id: int = Depends(get_current_user_id),
    page: int = Query(0, ge=0),
    per_page: int = Query(10, ge=1, le=50),
):
    invoices, total = await crud.get_all_invoices_paginated(
        page=page, per_page=per_page, user_id=user_id
    )
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": inv.id,
                "plan_key": inv.plan_key,
                "plan_title": inv.plan_title,
                "days": inv.days,
                "amount_rub": inv.amount_rub,
                "gateway": inv.gateway,
                "status": inv.status,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
                "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
            }
            for inv in invoices
        ],
    }


# ─── Public endpoints ─────────────────────────────────────────────────────────

@router.get("/plans")
async def get_plans():
    """Public endpoint — returns all active VPN plans."""
    plans = await crud.get_all_plans()
    return {
        "ok": True,
        "plans": [
            {
                "id": p.id,
                "title": p.title,
                "days": p.days,
                "price": p.price,
            }
            for p in plans
        ]
    }


@router.post("/user/trial")
async def claim_trial_endpoint(user_id: int = Depends(get_current_user_id)):
    user = await crud.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.test_taken:
        raise HTTPException(status_code=400, detail="Trial already used")
    if user.vpn_name:
        raise HTTPException(status_code=400, detail="You already have a VPN")

    from bot.services.delivery import deliver_vpn
    from bot.config import db_settings
    test_days = int(db_settings.get("test_days") or "3")
    success = await deliver_vpn(None, user_id, test_days)
    if not success:
        raise HTTPException(status_code=500, detail="Delivery failed")
        
    await crud.update_user_test_taken(user_id)
    return {"ok": True, "message": "Trial activated"}

@router.post("/user/reissue")
async def reissue_vpn_endpoint(user_id: int = Depends(get_current_user_id)):
    user = await crud.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.vpn_name:
        raise HTTPException(status_code=400, detail="No VPN to reissue")

    is_admin = user_id in settings.get_admin_ids
    reissue_cost = 50.0
    should_charge = not is_admin and user.free_reissue_used
    if should_charge and (user.balance or 0.0) < reissue_cost:
        raise HTTPException(status_code=402, detail="Reissue costs 50 ₽. Please top up your balance.")

    try:
        client_data = await vpn_service.get_client(user.vpn_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch client: {e}")

    info = vpn_service.normalize_client_payload(client_data or {})
    if int(info.get("left_days") or 0) <= 0:
        raise HTTPException(status_code=400, detail="Subscription is expired. Please renew it instead.")

    try:
        await vpn_service.reissue_client_same_name(
            user.vpn_name,
            default_days=30,
            default_limit_gb=int(db_settings.get("default_limit_gb") or "0"),
            device_limit=7,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reissue key: {e}")

    new_sub_token = await crud.regenerate_sub_token(user_id)
    if should_charge:
        await crud.add_user_balance(user_id, -reissue_cost)
    elif not is_admin:
        from bot.database.models import User
        async with crud.AsyncSessionLocal() as session:
            u = await session.get(User, user_id)
            if u:
                u.free_reissue_used = True
                await session.commit()
    
    return {"ok": True, "message": "Reissued successfully", "vpn_name": user.vpn_name, "sub_token": new_sub_token}
