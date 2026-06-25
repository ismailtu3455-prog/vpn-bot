from __future__ import annotations

import aiohttp
import logging
from bot.config import settings

logger = logging.getLogger(__name__)

async def send_verification_email(to_email: str, code: str) -> bool:
    """Send a 6-digit OTP code to the user via Resend API."""
    if not settings.resend_api_key:
        logger.error("Resend API key is not configured")
        return False
    
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background-color: #1a1a2e; color: #fff; border-radius: 10px; padding: 30px;">
        <h2 style="color: #4CAF50; text-align: center;">Вход в Adoria VPN</h2>
        <p style="font-size: 16px;">Здравствуйте!</p>
        <p style="font-size: 16px;">Ваш код для входа в личный кабинет:</p>
        <div style="background-color: #16213e; border: 1px solid #4CAF50; border-radius: 8px; padding: 15px; text-align: center; margin: 20px 0;">
            <span style="font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #4CAF50;">{code}</span>
        </div>
        <p style="font-size: 14px; color: #aaa;">Код действителен в течение 5 минут.</p>
        <p style="font-size: 12px; color: #777; margin-top: 30px; border-top: 1px solid #333; padding-top: 15px;">
            Если вы не запрашивали этот код, просто проигнорируйте это письмо.
        </p>
    </div>
    """

    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": "Код подтверждения Adoria VPN",
        "html": html_content
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.resend.com/emails", headers=headers, json=payload) as resp:
                if resp.status in (200, 201):
                    logger.info(f"Resend OTP sent successfully to {to_email}")
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"Resend API error: {resp.status} {text}")
                    return False
    except Exception as e:
        logger.error(f"Failed to send email to {to_email} via Resend: {e}")
        return False


async def send_gift_email(to_email: str, gift_link: str) -> bool:
    """Send a gift-card activation link via Resend API."""
    if not settings.resend_api_key:
        logger.error("Resend API key is not configured")
        return False

    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background-color: #1a1a2e; color: #fff; border-radius: 10px; padding: 30px;">
        <h2 style="color: #4CAF50; text-align: center;">Вам отправили VPN в подарок</h2>
        <p style="font-size: 16px;">Здравствуйте!</p>
        <p style="font-size: 16px;">Для активации подарка откройте ссылку в Telegram:</p>
        <div style="text-align: center; margin: 30px 0;">
            <a href="{gift_link}" style="background-color: #4CAF50; color: white; padding: 15px 25px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 18px;">Забрать подарок</a>
        </div>
        <p style="font-size: 14px; color: #aaa;">Или перейдите по ссылке:<br><a href="{gift_link}" style="color: #4CAF50;">{gift_link}</a></p>
    </div>
    """

    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": "Вам отправили VPN в подарок",
        "html": html_content,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.resend.com/emails", headers=headers, json=payload) as resp:
                if resp.status in (200, 201):
                    logger.info("Resend gift email sent successfully to %s", to_email)
                    return True
                text = await resp.text()
                logger.error("Resend gift email error: %s %s", resp.status, text)
                return False
    except Exception as e:
        logger.error("Failed to send gift email to %s via Resend: %s", to_email, e)
        return False
