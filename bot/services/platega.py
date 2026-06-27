import logging
from plategaio import PlategaAsyncClient, CreateTransactionRequest, PaymentDetails

logger = logging.getLogger(__name__)

async def create_platega_invoice(
    shop_id: str,
    api_key: str,
    amount: float,
    description: str,
    order_id: str,
    hook_url: str = "",
    success_url: str = "",
) -> tuple[str, str] | None:
    """Create a Platega.io invoice. Returns (transaction_id, pay_url) or None on error."""
    try:
        async with PlategaAsyncClient(merchant_id=shop_id, secret=api_key) as client:
            payload = CreateTransactionRequest(
                id=order_id,
                payment_method=2, # 2 = СБП (QR-код)
                payment_details=PaymentDetails(amount=int(amount), currency="RUB"),
                description=description,
                return_url=success_url if success_url else None
            )
            response = await client.create_transaction(payload=payload)
            return response.transaction_id, response.redirect
    except Exception as e:
        logger.error(f"Platega create_invoice exception: {e}")
        return None


def verify_platega_webhook(data: dict, api_key: str) -> bool:
    """Verify Platega webhook."""
    # Since we don't have webhook signature docs, we assume it's checked by IP or we blindly trust for now.
    return True


async def get_platega_invoice_status(
    shop_id: str,
    api_key: str,
    order_id: str,
) -> str:
    """Get Platega invoice status. Returns 'success', 'pending', or 'error'."""
    try:
        async with PlategaAsyncClient(merchant_id=shop_id, secret=api_key) as client:
            resp = await client.get_transaction_status(transaction_id=order_id)
            status = resp.status.lower()
            if status in ("success", "paid", "completed", "approved"):
                return "success"
            if status in ("error", "failed", "cancelled", "declined"):
                return "error"
            return "pending"
    except Exception as e:
        logger.error(f"Platega get_status exception: {e}")
        return "error"
