from __future__ import annotations

import logging
from typing import Optional

from aiocryptopay import AioCryptoPay, Networks

from bot.config import settings, db_settings

logger = logging.getLogger(__name__)

_client: Optional[AioCryptoPay] = None


def _get_client() -> AioCryptoPay | None:
    global _client
    token = db_settings.get("crypto_pay_token") or settings.crypto_pay_token
    if not token:
        return None
    if _client is None:
        _client = AioCryptoPay(token=token, network=Networks.MAIN_NET)
    return _client


def _reset_client() -> None:
    global _client
    _client = None


async def create_crypto_invoice(
    amount_rub: float,
    description: str,
    payload: str,
) -> tuple[str, str] | None:
    """
    Create a CryptoBot invoice.
    Returns (invoice_id, pay_url) or None on error.
    """
    client = _get_client()
    if client is None:
        logger.warning("CryptoPay: no token configured")
        return None
    try:
        usdt_rate = float(db_settings.get("usdt_rate") or "90")
        amount_usdt = round(amount_rub / usdt_rate, 2)
        if amount_usdt < 0.01:
            amount_usdt = 0.01
        currency = settings.crypto_currency or "USDT"
        invoice = await client.create_invoice(
            amount=amount_rub,
            fiat="RUB",
            currency_type="fiat",
            description=description,
            payload=payload,
        )
        return str(invoice.invoice_id), invoice.bot_invoice_url
    except Exception as e:
        logger.error(f"CryptoPay create_invoice error: {e}")
        return None


async def get_crypto_invoice_status(invoice_id: str) -> str:
    """Returns 'paid', 'active', or 'expired'."""
    client = _get_client()
    if client is None:
        return "expired"
    try:
        invoices = await client.get_invoices(invoice_ids=[int(invoice_id)])
        if not invoices:
            return "expired"
        inv = invoices[0]
        status = str(inv.status).lower()
        if status == "paid":
            return "paid"
        if status in ("expired", "cancelled"):
            return "expired"
        return "active"
    except Exception as e:
        logger.error(f"CryptoPay get_status error: {e}")
        return "expired"


async def close_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
        _client = None
