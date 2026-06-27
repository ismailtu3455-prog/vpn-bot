from fastapi import APIRouter, Request, HTTPException
import json
import logging

from bot.database import crud
from bot.services.delivery import deliver_vpn, deliver_gift
from bot.config import db_settings, settings
from bot.services import yoomoney, platega

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

@router.post("/yoomoney")
async def yoomoney_webhook(request: Request):
    data = await request.form()
    data_dict = dict(data)
    label = data_dict.get("label", "")
    operation_id = data_dict.get("operation_id", "")
    logger.info("YooMoney webhook received: operation_id=%s label=%s", operation_id, label or "—")
    
    secret = db_settings.get("yoomoney_secret")
    if not secret:
        raise HTTPException(status_code=400, detail="YooMoney not configured")
        
    if not yoomoney.verify_notification(data_dict, secret):
        logger.warning("YooMoney webhook rejected: invalid signature for label=%s", label or "—")
        raise HTTPException(status_code=400, detail="Invalid signature")
        
    if not label:
        logger.info("YooMoney webhook ignored: empty label")
        return {"status": "ignored"}
        
    parsed_label = yoomoney.get_label_info(label)
    if not parsed_label:
        logger.info("YooMoney webhook ignored: malformed label=%s", label)
        return {"status": "ignored"}
    user_id, plan_id, invoice_id = parsed_label
        
    target_invoice = await crud.get_invoice_by_label(label)
    if not target_invoice and invoice_id:
        target_invoice = await crud.get_invoice(invoice_id)
            
    if (
        target_invoice
        and target_invoice.user_id == user_id
        and target_invoice.plan_key == plan_id
        and target_invoice.gateway == "yoomoney"
        and target_invoice.status == "active"
    ):
        if target_invoice.is_gift and not target_invoice.gift_for_user_id:
            logger.info("YooMoney webhook accepted for pending gift invoice=%s", target_invoice.invoice_id)
            return {"status": "ok"}
        await crud.update_invoice_status(target_invoice.invoice_id, "paid")
        logger.info("YooMoney invoice marked paid: invoice_id=%s", target_invoice.invoice_id)
        
        target_user_id = target_invoice.gift_for_user_id if target_invoice.is_gift else target_invoice.user_id
        if target_invoice.is_gift and target_invoice.gift_for_user_id:
            recipient = await crud.get_user(target_invoice.gift_for_user_id)
            if recipient and recipient.email:
                success = await deliver_gift(
                    target_invoice.user_id,
                    target_invoice.days,
                    recipient_email=recipient.email,
                )
                if not success:
                    raise HTTPException(status_code=500, detail="Failed to deliver gift email")
            else:
                await deliver_vpn(None, target_invoice.gift_for_user_id, target_invoice.days, is_gift=True)
        elif target_user_id:
            if target_invoice.plan_key == "reissue":
                from bot.handlers.user import _do_reissue
                u = await crud.get_user(target_user_id)
                if u:
                    await _do_reissue(None, target_user_id, u.vpn_name)
            else:
                await deliver_vpn(None, target_user_id, target_invoice.days, is_gift=target_invoice.is_gift)
            
        user = await crud.get_user(target_invoice.user_id)
        if user and user.ref_id:
            await crud.process_referral_bonus(user.ref_id, target_invoice.amount_rub)
    else:
        logger.info("YooMoney webhook did not match an active invoice for label=%s", label)
        
    return {"status": "ok"}


@router.post("/platega")
async def platega_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    api_key = db_settings.get("platega_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="Platega not configured")
        
    if not platega.verify_platega_webhook(data, api_key):
        raise HTTPException(status_code=400, detail="Invalid signature")
        
    order_id = data.get("id") or data.get("transactionId") or data.get("order_id")
    status = data.get("status", "").upper()
    
    if not order_id or status != "CONFIRMED":
        return {"status": "ok"}
        
    invoice = await crud.get_invoice(order_id)
    if invoice and invoice.status == "active":
        if invoice.is_gift and not invoice.gift_for_user_id:
            return {"status": "ok"}
        await crud.update_invoice_status(order_id, "paid")
        
        target_user_id = invoice.gift_for_user_id if invoice.is_gift else invoice.user_id
        if invoice.is_gift and invoice.gift_for_user_id:
            recipient = await crud.get_user(invoice.gift_for_user_id)
            if recipient and recipient.email:
                success = await deliver_gift(
                    invoice.user_id,
                    invoice.days,
                    recipient_email=recipient.email,
                )
                if not success:
                    raise HTTPException(status_code=500, detail="Failed to deliver gift email")
            else:
                await deliver_vpn(None, invoice.gift_for_user_id, invoice.days, is_gift=True)
        elif target_user_id:
            if invoice.plan_key == "reissue":
                from bot.handlers.user import _do_reissue
                u = await crud.get_user(target_user_id)
                if u:
                    await _do_reissue(None, target_user_id, u.vpn_name)
            else:
                await deliver_vpn(None, target_user_id, invoice.days, is_gift=invoice.is_gift)
            
        user = await crud.get_user(invoice.user_id)
        if user and user.ref_id:
            await crud.process_referral_bonus(user.ref_id, invoice.amount_rub)
        
    return {"status": "ok"}


import hashlib
import hmac

@router.post("/cryptopay")
async def cryptopay_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("crypto-pay-api-signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing signature")
        
    token = db_settings.get("crypto_pay_token") or settings.crypto_pay_token
    if not token:
        raise HTTPException(status_code=400, detail="CryptoPay not configured")
        
    secret = hashlib.sha256(token.encode()).digest()
    expected_signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    
    if signature != expected_signature:
        raise HTTPException(status_code=400, detail="Invalid signature")
        
    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    update_type = data.get("update_type")
    if update_type == "invoice_paid":
        payload = data.get("payload") or data.get("invoice") or {}
        invoice_id = str(payload.get("invoice_id"))
        
        # We need to find the invoice by our own ID. Wait, CryptoBot creates an invoice, and we store its ID.
        # But payload.invoice_id is the CryptoBot's ID. Let's see how create_invoice stores it.
        # In cryptopay.py: `return str(invoice.invoice_id), invoice.bot_invoice_url`.
        # And in user.py / payments.py: `await crud.create_invoice(..., invoice_id=inv_id)`
        # So yes, invoice_id is exactly the CryptoBot invoice ID.
        
        invoice = await crud.get_invoice(invoice_id)
        if invoice and invoice.status == "active":
            if invoice.is_gift and not invoice.gift_for_user_id:
                return {"status": "ok"}
            await crud.update_invoice_status(invoice_id, "paid")
            
            target_user_id = invoice.gift_for_user_id if invoice.is_gift else invoice.user_id
            if invoice.is_gift and invoice.gift_for_user_id:
                recipient = await crud.get_user(invoice.gift_for_user_id)
                if recipient and recipient.email:
                    success = await deliver_gift(
                        invoice.user_id,
                        invoice.days,
                        recipient_email=recipient.email,
                    )
                    if not success:
                        raise HTTPException(status_code=500, detail="Failed to deliver gift email")
                else:
                    await deliver_vpn(None, invoice.gift_for_user_id, invoice.days, is_gift=True)
            elif target_user_id:
                if invoice.plan_key == "reissue":
                    from bot.handlers.user import _do_reissue
                    u = await crud.get_user(target_user_id)
                    if u:
                        await _do_reissue(None, target_user_id, u.vpn_name)
                else:
                    await deliver_vpn(None, target_user_id, invoice.days, is_gift=invoice.is_gift)
                
            user = await crud.get_user(invoice.user_id)
            if user and user.ref_id:
                await crud.process_referral_bonus(user.ref_id, invoice.amount_rub)
            
    return {"status": "ok"}

