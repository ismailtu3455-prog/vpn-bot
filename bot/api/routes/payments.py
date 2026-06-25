from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
import logging
import uuid
from typing import Optional

from bot.database import crud
from bot.services.delivery import deliver_vpn, deliver_gift
from bot.config import settings, db_settings
from bot.services import cryptopay, yoomoney, lava

router = APIRouter()
logger = logging.getLogger(__name__)

async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ")[1]
    user_id = await crud.get_user_by_web_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session")
    user = await crud.get_user(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

class CheckoutRequest(BaseModel):
    plan_id: str
    gateway: str
    is_gift: bool = False
    gift_for_email: Optional[str] = None
    gift_for_user_id: Optional[int] = None

@router.post("/pay/checkout")
async def create_checkout(body: CheckoutRequest, user = Depends(get_current_user)):
    plan = await crud.get_plan(body.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    # Handle gift logic
    gift_for_user_id = None
    if body.is_gift:
        if body.gift_for_user_id:
            gift_for_user_id = body.gift_for_user_id
        elif body.gift_for_email:
            # Find user by email
            recipient = await crud.get_user_by_email(body.gift_for_email)
            if recipient:
                gift_for_user_id = recipient.user_id
            else:
                import random
                # Create user for unknown email
                new_user_id = random.randint(10000000000, 99999999999)
                username = body.gift_for_email.split('@')[0]
                await crud.register_user(
                    user_id=new_user_id,
                    username=username,
                    first_name="Web",
                    last_name="User",
                    ref_id=None
                )
                await crud.set_user_email(new_user_id, body.gift_for_email)
                gift_for_user_id = new_user_id
    
    price = plan.price
    promo_applied = False
    if user.promo_used and not body.is_gift:
        promo = await crud.get_promocode(user.promo_used)
        if promo and promo.promo_type == "discount":
            discount = round(price * promo.value / 100, 2)
            price = round(price - discount, 2)
            promo_applied = True
            
    if body.gateway == "balance":
        if (user.balance or 0.0) < price:
            raise HTTPException(status_code=400, detail="Not enough balance")
        await crud.add_user_balance(user.user_id, -price)
        invoice_id = f"bal_{uuid.uuid4().hex[:12]}"
        await crud.create_invoice(
            user_id=user.user_id,
            plan_key=plan.id,
            plan_title=plan.title,
            days=plan.days,
            amount_rub=price,
            gateway="balance",
            invoice_id=invoice_id,
            is_gift=body.is_gift,
            gift_for_user_id=gift_for_user_id
        )
        await crud.update_invoice_status(invoice_id, "paid")
        
        if body.is_gift and body.gift_for_email:
            success = await deliver_gift(user.user_id, plan.days, recipient_email=body.gift_for_email)
        else:
            target_user_id = gift_for_user_id if body.is_gift else user.user_id
            success = await deliver_vpn(None, target_user_id, plan.days)
        if not success:
            raise HTTPException(status_code=500, detail="Error delivering VPN")
        if promo_applied:
            await crud.clear_user_promo(user.user_id)
        return {"ok": True, "redirect_url": settings.dashboard_url + "?success=1"}
        
    elif body.gateway in ("crypto", "cryptopay"):
        payload = f"user:{user.user_id}:plan:{plan.id}:gift:{1 if body.is_gift else 0}"
        result = await cryptopay.create_crypto_invoice(
            amount_rub=price,
            description=f"Adoria VPN - {plan.title}",
            payload=payload
        )
        if not result:
            raise HTTPException(status_code=500, detail="CryptoBot is unavailable")
        invoice_id, pay_url = result
        # Create invoice record in database
        await crud.create_invoice(
            user_id=user.user_id,
            plan_key=plan.id,
            plan_title=plan.title,
            days=plan.days,
            amount_rub=price,
            gateway="cryptopay",
            invoice_id=invoice_id,
            is_gift=body.is_gift,
            gift_for_user_id=gift_for_user_id
        )
        # Return Telegram bot payment link - CryptoBot returns bot_invoice_url for Telegram redirect
        return {"ok": True, "redirect_url": pay_url}
        
    elif body.gateway == "yoomoney":
        invoice_id = f"yoo_{uuid.uuid4().hex[:12]}"
        label = f"{user.user_id}:{plan.id}:{invoice_id}"
        await crud.create_invoice(
            user_id=user.user_id,
            plan_key=plan.id,
            plan_title=plan.title,
            days=plan.days,
            amount_rub=price,
            gateway="yoomoney",
            invoice_id=invoice_id,
            label=label,
            is_gift=body.is_gift,
            gift_for_user_id=gift_for_user_id
        )
        pay_url = await yoomoney.create_yoomoney_invoice(
            amount=price,
            label=label,
            description=f"Adoria VPN - {plan.title}"
        )
        return {"ok": True, "redirect_url": pay_url}

    elif body.gateway == "lava":
        shop_id = db_settings.get("lava_shop_id")
        api_key = db_settings.get("lava_api_key")
        if not shop_id or not api_key:
            raise HTTPException(status_code=500, detail="Lava is not configured")
        invoice_id = f"lava_{uuid.uuid4().hex[:12]}"
        hook_url = settings.lava_webhook_url
        success_url = settings.dashboard_url + "?success=1"
        result = await lava.create_lava_invoice(
            shop_id=shop_id,
            api_key=api_key,
            amount=price,
            description=f"Adoria VPN - {plan.title}",
            order_id=invoice_id,
            hook_url=hook_url,
            success_url=success_url,
        )
        if not result:
            raise HTTPException(status_code=500, detail="Lava is unavailable")
        _, pay_url = result
        await crud.create_invoice(
            user_id=user.user_id,
            plan_key=plan.id,
            plan_title=plan.title,
            days=plan.days,
            amount_rub=price,
            gateway="lava",
            invoice_id=invoice_id,
            label=invoice_id,
            is_gift=body.is_gift,
            gift_for_user_id=gift_for_user_id,
        )
        return {"ok": True, "redirect_url": pay_url}
        
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported gateway: {body.gateway}")
