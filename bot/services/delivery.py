from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING

from bot.config import db_settings, settings
from bot.database import crud
from bot.services.email import send_gift_email
from bot.services import vpn as vpn_service

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

SUB_BASE_URL = f"https://{settings.subscription_domain}"


async def deliver_vpn(
    bot: "Bot" | None,
    user_id: int,
    days: int,
    is_gift: bool = False,
) -> bool:
    """
    Deliver or extend a VPN subscription to a user.

    Creates the client on ALL configured 3X-UI servers in all active inbounds.
    The subscription URL on our FastAPI aggregates links from all servers.

    Returns True on success, False on error.
    """
    try:
        user = await crud.get_user(user_id)
        if user is None:
            logger.error(f"deliver_vpn: user {user_id} not found in DB")
            return False

        limit_gb = int(db_settings.get("default_limit_gb") or "0")
        is_new_client = False

        if user.vpn_name:
            # Try to extend existing client on all servers
            try:
                await vpn_service.extend_client(user.vpn_name, days)
                logger.info(f"Extended VPN for {user_id}: {user.vpn_name} +{days}d")
            except vpn_service.VPNAPIError as e:
                if e.status == 404:
                    # Client not found on any server — re-create
                    logger.warning(
                        f"VPN client {user.vpn_name} not found on any server, re-creating"
                    )
                    vpn_name = _gen_name(user_id)
                    await vpn_service.create_client(vpn_name, days, limit_gb)
                    await crud.set_vpn_name(user_id, vpn_name)
                    user = await crud.get_user(user_id)
                    is_new_client = True
                else:
                    raise
        else:
            # First-time — create on all servers
            vpn_name = _gen_name(user_id)
            await vpn_service.create_client(vpn_name, days, limit_gb)
            await crud.set_vpn_name(user_id, vpn_name)
            user = await crud.get_user(user_id)
            is_new_client = True

        if not user or not user.vpn_name:
            logger.error(f"deliver_vpn: vpn_name still None for {user_id}")
            return False

        # ── Build subscription URL (our aggregator) ──────────────────────────
        sub_url = f"{SUB_BASE_URL}/sub/{user.sub_token}"

        # ── Collect traffic info from first available server ──────────────────
        left_days = days
        expires_at = ""
        used_gb = 0.0
        limit = limit_gb

        try:
            client_info = vpn_service.normalize_client_payload(
                await vpn_service.get_client(user.vpn_name)
            )
            left_days = client_info.get("left_days") or days
            expires_at = client_info.get("expires_at") or ""
            used_gb = round(
                (client_info.get("used_traffic_bytes") or 0) / 1_073_741_824, 2
            )
            limit = client_info.get("traffic_limit_gb") or limit_gb
        except vpn_service.VPNAPIError as e:
            logger.warning(f"Could not fetch client info for {user.vpn_name}: {e}")

        # ── Build message ─────────────────────────────────────────────────────
        traffic_line = (
            f"📊 Трафик: <b>{used_gb} ГБ</b> / {'∞' if limit == 0 else f'{limit} ГБ'}\n"
        )
        expires_line = f"📅 Истекает: <b>{expires_at}</b>\n" if expires_at else ""

        if is_gift:
            action_text = "VPN подарен и активирован!"
        elif is_new_client:
            action_text = "VPN активирован!"
        else:
            action_text = "VPN продлён!"

        text = (
            f"✅ <b>{action_text}</b>\n\n"
            f"👤 Профиль: <code>{user.vpn_name}</code>\n"
            f"⏳ Дней осталось: <b>{left_days}</b>\n"
            f"{expires_line}"
            f"{traffic_line}\n"
            f"🔗 <b>Ссылка-подписка</b> (автообновление):\n"
            f"<code>{sub_url}</code>\n\n"
            f"📱 <b>Вставьте ссылку в приложение</b> (Hiddify, v2rayNG, Streisand и др.)\n\n"
            f"🌍 В подписке доступны все серверы: 🇩🇪 Германия и другие по мере добавления."
        )

        try:
            if bot is not None:
                await bot.send_message(user_id, text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Could not send telegram message to {user_id}: {e}")

        return True

    except vpn_service.VPNAPIError as e:
        logger.error(f"deliver_vpn VPN API error for {user_id}: {e}")
        try:
            if bot is not None:
                await bot.send_message(
                    user_id,
                    "⚠️ Произошла ошибка при создании VPN. Администратор уведомлён. "
                    "Обратитесь в поддержку если проблема не решится.",
                    parse_mode="HTML",
                )
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"deliver_vpn unexpected error for {user_id}: {e}", exc_info=True)
        return False


def _gen_name(user_id: int) -> str:
    suffix = secrets.token_hex(2)
    return f"tg{user_id}{suffix}"


async def deliver_gift(
    buyer_user_id: int,
    days: int,
    recipient_email: str | None = None,
    bot: "Bot" | None = None,
) -> bool:
    """Create a gift card and deliver it by email or Telegram message to the buyer."""
    gc = await crud.create_gift_card(buyer_user_id, days)
    gift_link = f"https://t.me/{settings.bot_username}?start=gift_{gc.code}"

    if recipient_email:
        return await send_gift_email(recipient_email, gift_link)

    if bot is not None:
        try:
            from bot.keyboards.inline import gift_sent_kb

            await bot.send_message(
                buyer_user_id,
                f"✅ <b>Подарочный ключ готов!</b>\n\n"
                f"🎁 <b>На {days} дней:</b>\n"
                f"<code>{gift_link}</code>",
                reply_markup=gift_sent_kb(gc.code),
                parse_mode="HTML",
            )
            return True
        except Exception as e:
            logger.warning("Could not deliver gift link to %s: %s", buyer_user_id, e)
            return False

    return False
