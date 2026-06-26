from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from bot.api.auth import require_admin_key
from bot.config import settings, db_settings
from bot.database import crud
from bot.services import vpn as vpn_service
from bot.services.delivery import deliver_vpn, deliver_gift

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin")

# Bot reference
_bot = None


def set_bot(bot) -> None:
    global _bot
    _bot = bot


# ─── Schemas ──────────────────────────────────────────────────────────────────

class GrantBody(BaseModel):
    days: int


class BalanceBody(BaseModel):
    amount: float


class BanVpnBody(BaseModel):
    reason: str = "Admin action"


class CreatePlanBody(BaseModel):
    id: str
    title: str
    days: int
    price: float

class UpdatePlanBody(BaseModel):
    title: Optional[str] = None
    days: Optional[int] = None
    price: Optional[float] = None
    is_active: Optional[bool] = None


class CreatePromoBody(BaseModel):
    code: str
    promo_type: str
    value: float
    max_uses: int = 1


class SettingPatchBody(BaseModel):
    key: str
    value: str


class AddAdminBody(BaseModel):
    email: str


# ─── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(_key: str = Depends(require_admin_key)):
    stats = await crud.get_dashboard_stats_extended()
    stats["active_vpn"] = stats.get("users_with_vpn", 0)
    stats["total_revenue"] = stats.get("revenue", 0.0)
    stats["total_invoices"] = stats.get("paid_invoices", 0)
    return stats


# ─── Admins ───────────────────────────────────────────────────────────────────

@router.get("/admins")
async def admin_list_admins(_key: str = Depends(require_admin_key)):
    """Get list of all admins"""
    admins = await crud.get_admins()
    return {
        "ok": True,
        "admins": [
            {
                "user_id": a.user_id,
                "added_at": a.added_at.isoformat() if a.added_at else None,
            }
            for a in admins
        ]
    }


@router.post("/admins")
async def admin_add_admin(body: AddAdminBody, _key: str = Depends(require_admin_key)):
    """Add admin by email - finds user by email and grants admin access"""
    user = await crud.get_user_by_email(body.email)
    if not user:
        raise HTTPException(status_code=404, detail=f"User with email {body.email} not found")
    
    await crud.add_admin(user.user_id)
    return {"ok": True, "user_id": user.user_id, "email": body.email}


@router.delete("/admins/{user_id}")
async def admin_remove_admin(user_id: int, _key: str = Depends(require_admin_key)):
    """Remove admin access"""
    user = await crud.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    await crud.remove_admin(user_id)
    return {"ok": True, "user_id": user_id}


# ─── Users ────────────────────────────────────────────────────────────────────

@router.get("/users")
async def admin_users(
    page: int = Query(0, ge=0),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    _key: str = Depends(require_admin_key),
):
    users, total = await crud.get_users_paginated(page=page, per_page=per_page, search=search)
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "users": [
            {
                "user_id": u.user_id,
                "username": u.username,
                "first_name": u.first_name,
                "balance": u.balance,
                "vpn_name": u.vpn_name,
                "vpn_banned": u.vpn_banned,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
    }


@router.get("/users/{user_id}")
async def admin_user_detail(user_id: int, _key: str = Depends(require_admin_key)):
    user = await crud.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    vpn_info: dict = {}
    if user.vpn_name:
        try:
            data = await vpn_service.get_client(user.vpn_name)
            vpn_info = vpn_service.normalize_client_payload(data)
        except Exception:
            pass

    refs = await crud.get_referrals_count(user_id)
    paid = await crud.get_paid_invoices_count(user_id)
    user_payload = {
        "user_id": user.user_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "balance": user.balance,
        "ref_earned": user.ref_earned,
        "vpn_name": user.vpn_name,
        "vpn_banned": user.vpn_banned,
        "referrals_count": refs,
        "paid_invoices": paid,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "vpn_info": vpn_info,
    }
    vpn_payload = {
        "days_left": vpn_info.get("left_days", 0),
        "used_bytes": vpn_info.get("used_traffic_bytes", 0),
        "expires_at": vpn_info.get("expires_at"),
        "is_banned": vpn_info.get("is_banned", user.vpn_banned),
    } if vpn_info else None
    return {**user_payload, "user": user_payload, "vpn": vpn_payload}


@router.delete("/users/{user_id}")
async def admin_delete_user(user_id: int, _key: str = Depends(require_admin_key)):
    user = await crud.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.vpn_name:
        try:
            await vpn_service.delete_client(user.vpn_name)
        except Exception:
            pass
    await crud.delete_user(user_id)
    return {"ok": True, "user_id": user_id}


@router.post("/users/{user_id}/grant")
async def admin_grant_vpn(
    user_id: int,
    body: GrantBody,
    _key: str = Depends(require_admin_key),
):
    if not _bot:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    success = await deliver_vpn(_bot, user_id, body.days)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to deliver VPN")
    return {"ok": True, "days": body.days}


@router.post("/users/{user_id}/balance")
async def admin_add_balance(
    user_id: int,
    body: BalanceBody,
    _key: str = Depends(require_admin_key),
):
    user = await crud.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await crud.add_user_balance(user_id, body.amount)
    return {"ok": True, "amount": body.amount}


@router.post("/users/{user_id}/ban-vpn")
async def admin_ban_vpn(
    user_id: int,
    body: BanVpnBody,
    _key: str = Depends(require_admin_key),
):
    user = await crud.get_user(user_id)
    if not user or not user.vpn_name:
        raise HTTPException(status_code=404, detail="User VPN not found")
    try:
        await vpn_service.ban_client(user.vpn_name, reason=body.reason)
        await crud.set_user_vpn_banned(user_id, True)
        if _bot:
            from bot.texts import Texts
            try:
                await _bot.send_message(user_id, Texts.BANNED_VPN.format(reason=body.reason), parse_mode="HTML")
            except Exception:
                pass
        return {"ok": True}
    except vpn_service.VPNAPIError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/unban-vpn")
async def admin_unban_vpn(
    user_id: int,
    _key: str = Depends(require_admin_key),
):
    user = await crud.get_user(user_id)
    if not user or not user.vpn_name:
        raise HTTPException(status_code=404, detail="User VPN not found")
    try:
        await vpn_service.unban_client(user.vpn_name)
        await crud.set_user_vpn_banned(user_id, False)
        if _bot:
            from bot.texts import Texts
            try:
                await _bot.send_message(user_id, Texts.UNBANNED_VPN, parse_mode="HTML")
            except Exception:
                pass
        return {"ok": True}
    except vpn_service.VPNAPIError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Invoices ─────────────────────────────────────────────────────────────────

@router.get("/invoices")
async def admin_invoices(
    page: int = Query(0, ge=0),
    per_page: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    _key: str = Depends(require_admin_key),
):
    if status == "canceled":
        status = "cancelled"
    invoices, total = await crud.get_all_invoices_paginated(page=page, per_page=per_page, status=status)
    items = [
        {
            "id": inv.id,
            "user_id": inv.user_id,
            "plan_key": inv.plan_key,
            "plan_title": inv.plan_title,
            "plan": inv.plan_title,
            "days": inv.days,
            "amount_rub": inv.amount_rub,
            "amount": inv.amount_rub,
            "asset": "₽",
            "gateway": inv.gateway,
            "invoice_id": inv.invoice_id,
            "status": inv.status,
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
            "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
        }
        for inv in invoices
    ]
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": items,
        "invoices": items,
    }


@router.post("/invoices/{invoice_id}/approve")
async def admin_approve_invoice(
    invoice_id: str,
    _key: str = Depends(require_admin_key),
):
    inv = await crud.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await crud.update_invoice_status(invoice_id, "paid")
    if not _bot:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    if inv.is_gift and not inv.gift_for_user_id:
        await deliver_gift(inv.user_id, inv.days, bot=_bot)
        success = True
    elif inv.is_gift and inv.gift_for_user_id:
        recipient = await crud.get_user(inv.gift_for_user_id)
        if recipient and recipient.email:
            success = await deliver_gift(inv.user_id, inv.days, recipient_email=recipient.email)
        else:
            success = await deliver_vpn(_bot, inv.gift_for_user_id, inv.days, is_gift=True)
    else:
        recipient_id = inv.gift_for_user_id if inv.is_gift else inv.user_id
        success = await deliver_vpn(_bot, recipient_id, inv.days, is_gift=inv.is_gift)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to deliver VPN")
    return {"ok": True}


# ─── Plans ────────────────────────────────────────────────────────────────────

@router.get("/plans")
async def admin_get_plans(_key: str = Depends(require_admin_key)):
    plans = await crud.get_all_plans(include_inactive=True)
    return {"plans": [{"id": p.id, "title": p.title, "days": p.days, "price": p.price, "is_active": p.is_active} for p in plans]}


@router.post("/plans")
async def admin_create_plan(body: CreatePlanBody, _key: str = Depends(require_admin_key)):
    plan = await crud.create_plan(body.id, body.title, body.days, body.price)
    return {"ok": True, "plan": {"id": plan.id, "title": plan.title, "days": plan.days, "price": plan.price}}


@router.delete("/plans/{plan_id}")
async def admin_delete_plan(plan_id: str, _key: str = Depends(require_admin_key)):
    await crud.delete_plan(plan_id)
    return {"ok": True}

@router.patch("/plans/{plan_id}")
async def admin_update_plan(plan_id: str, body: UpdatePlanBody, _key: str = Depends(require_admin_key)):
    plan = await crud.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
        
    async with crud.AsyncSessionLocal() as session:
        from sqlalchemy import update
        from bot.database.models import Plan
        
        updates = {}
        if body.title is not None:
            updates["title"] = body.title
        if body.days is not None:
            updates["days"] = body.days
        if body.price is not None:
            updates["price"] = body.price
        if body.is_active is not None:
            updates["is_active"] = body.is_active
            
        if updates:
            await session.execute(update(Plan).where(Plan.id == plan_id).values(**updates))
            await session.commit()
            
    return {"ok": True}


# ─── Promos ───────────────────────────────────────────────────────────────────

@router.get("/promos")
async def admin_get_promos(_key: str = Depends(require_admin_key)):
    promos = await crud.get_all_promocodes()
    return {
        "promos": [
            {
                "code": p.code,
                "promo_type": p.promo_type,
                "value": p.value,
                "max_uses": p.max_uses,
                "uses_count": p.uses_count,
                "is_active": p.is_active,
            }
            for p in promos
        ]
    }


@router.post("/promos")
async def admin_create_promo(body: CreatePromoBody, _key: str = Depends(require_admin_key)):
    promo = await crud.create_promocode(body.code, body.promo_type, body.value, body.max_uses)
    return {"ok": True, "promo": {"code": promo.code, "promo_type": promo.promo_type}}


@router.delete("/promos/{code}")
async def admin_delete_promo(code: str, _key: str = Depends(require_admin_key)):
    await crud.delete_promocode(code)
    return {"ok": True}


# ─── VPN Servers ──────────────────────────────────────────────────────────────

@router.get("/vpn-servers")
async def admin_vpn_servers(_key: str = Depends(require_admin_key)):
    """Return status from all configured VPN servers."""
    try:
        statuses = await vpn_service.get_all_server_statuses()
        return {"ok": True, "servers": statuses}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/vpn-logs")
async def admin_vpn_logs(
    count: int = Query(50, ge=1, le=500),
    _key: str = Depends(require_admin_key),
):
    try:
        logs = await vpn_service.get_logs(count)
        return {"ok": True, "logs": logs.get("data") or logs}
    except vpn_service.VPNAPIError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ─── Settings ─────────────────────────────────────────────────────────────────

SAFE_SETTINGS = {
    "stars_enabled", "test_enabled", "test_days", "default_limit_gb",
    "ref_reward_start", "ref_percent_lvl1", "ref_percent_lvl2",
    "balance_pay_enabled", "tome_enabled", "usdt_rate",
    "yoomoney_wallet", "platega_shop_id", "tome_phone", "tome_bank",
}


@router.get("/settings")
async def admin_get_settings(_key: str = Depends(require_admin_key)):
    safe: dict = {}
    for k, v in db_settings.items():
        if k in SAFE_SETTINGS:
            safe[k] = v
    safe["crypto_configured"] = bool(db_settings.get("crypto_pay_token") or settings.crypto_pay_token)
    safe["yoomoney_configured"] = bool(db_settings.get("yoomoney_wallet") and db_settings.get("yoomoney_secret"))
    safe["platega_configured"] = bool(db_settings.get("platega_shop_id") and db_settings.get("platega_api_key"))
    return {"settings": safe, **safe}


@router.patch("/settings")
async def admin_patch_setting(body: SettingPatchBody, _key: str = Depends(require_admin_key)):
    await crud.set_setting(body.key, body.value)
    return {"ok": True, "key": body.key, "value": body.value}
