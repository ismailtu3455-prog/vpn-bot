from __future__ import annotations

import hashlib
import logging
import urllib.parse

logger = logging.getLogger(__name__)

YOOMONEY_QUICKPAY_URL = "https://yoomoney.ru/quickpay/confirm"


def generate_payment_link(
    wallet: str,
    amount: float,
    label: str,
    desc: str,
    payment_type: str = "AC",
) -> str:
    """
    Generate a YooMoney quickpay link.
    payment_type: AC = bank card, PC = YooMoney wallet, SB = Sberbank Online
    """
    params = {
        "receiver": wallet,
        "quickpay-form": "button",
        "targets": desc,
        "sum": str(amount),
        "label": label,
        "paymentType": payment_type,
    }
    return f"{YOOMONEY_QUICKPAY_URL}?{urllib.parse.urlencode(params)}"


def generate_all_payment_links(wallet: str, amount: float, label: str, desc: str) -> dict[str, str]:
    """Return multiple payment links for different payment types."""
    return {
        "card": generate_payment_link(wallet, amount, label, desc, "AC"),
        "yoomoney": generate_payment_link(wallet, amount, label, desc, "PC"),
        "sberbank": generate_payment_link(wallet, amount, label, desc, "SB"),
    }


async def create_yoomoney_invoice(amount: float, label: str, description: str) -> str:
    """Compatibility helper used by the web checkout route."""
    from bot.config import db_settings, settings

    wallet = db_settings.get("yoomoney_wallet") or settings.yoomoney_wallet
    if not wallet:
        raise ValueError("YooMoney wallet is not configured")
    return generate_payment_link(wallet, amount, label, description, "AC")


def verify_notification(data: dict, secret: str) -> bool:
    """
    Verify YooMoney payment notification via SHA1 hash.
    Hash is computed over: notification_type&operation_id&amount&currency&datetime&sender&codepro&notification_secret&label
    """
    try:
        check_str = "&".join([
            data.get("notification_type", ""),
            data.get("operation_id", ""),
            data.get("amount", ""),
            data.get("currency", ""),
            data.get("datetime", ""),
            data.get("sender", ""),
            data.get("codepro", ""),
            secret,
            data.get("label", ""),
        ])
        expected = hashlib.sha1(check_str.encode("utf-8")).hexdigest()
        received = data.get("sha1_hash", "")
        result = expected == received
        if not result:
            logger.warning(
                f"YooMoney hash mismatch. Expected: {expected}, Got: {received}"
            )
        return result
    except Exception as e:
        logger.error(f"YooMoney verify_notification error: {e}")
        return False


def get_label_info(label: str) -> tuple[int, str, str | None] | None:
    """
    Parse label format 'user_id:plan_id[:invoice_id]'.
    Returns (user_id, plan_id, invoice_id) or None on error.
    """
    try:
        parts = label.split(":", 2)
        if len(parts) >= 2 and parts[0].isdigit():
            invoice_id = parts[2] if len(parts) == 3 else None
            return int(parts[0]), parts[1], invoice_id
        return None
    except Exception as e:
        logger.error(f"YooMoney get_label_info error: {e}")
        return None
