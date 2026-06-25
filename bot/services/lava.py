from __future__ import annotations

import hashlib
import hmac
import json
import logging
import aiohttp

logger = logging.getLogger(__name__)

LAVA_API_BASE = "https://api.lava.ru"


async def create_lava_invoice(
    shop_id: str,
    api_key: str,
    amount: float,
    description: str,
    order_id: str,
    hook_url: str = "",
    success_url: str = "",
) -> tuple[str, str] | None:
    """Create a Lava.ru invoice via СБП. Returns (order_id, pay_url) or None on error."""
    body: dict = {
        "shop_id": shop_id,
        "sum": amount,
        "order_id": order_id,
        "comment": description,
        "hook_url": hook_url,
        "success_url": success_url,
        "fail_url": "",
        "expire": 60,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{LAVA_API_BASE}/business/invoice/create",
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                logger.debug(f"Lava create response: {data}")
                if data.get("status") == 200 or data.get("id"):
                    inv_data = data.get("data") or data
                    pay_url = inv_data.get("url") or inv_data.get("pay_url") or ""
                    inv_id = inv_data.get("id") or order_id
                    return str(inv_id), pay_url
                logger.warning(f"Lava invoice error: {data}")
                return None
    except Exception as e:
        logger.error(f"Lava create_invoice exception: {e}")
        return None


def verify_lava_webhook(data: dict, api_key: str) -> bool:
    """Verify Lava webhook HMAC-SHA256 signature."""
    try:
        received_sign = data.get("sign", "")
        body_without_sign = {k: v for k, v in data.items() if k != "sign"}
        body_str = json.dumps(body_without_sign, sort_keys=True, ensure_ascii=False)
        expected = hmac.new(
            api_key.encode("utf-8"),
            body_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, received_sign)
    except Exception as e:
        logger.error(f"Lava verify_webhook error: {e}")
        return False


async def get_lava_invoice_status(
    shop_id: str,
    api_key: str,
    order_id: str,
) -> str:
    """Get Lava invoice status. Returns 'success', 'pending', or 'error'."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{LAVA_API_BASE}/business/invoice/status",
                params={"shop_id": shop_id, "order_id": order_id},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                inv_data = data.get("data") or data
                status = (inv_data.get("status") or "pending").lower()
                if status in ("success", "paid", "completed"):
                    return "success"
                if status in ("error", "failed", "cancelled"):
                    return "error"
                return "pending"
    except Exception as e:
        logger.error(f"Lava get_status exception: {e}")
        return "error"
