import asyncio
import logging
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand

from bot.config import settings, db_settings
from bot.database import crud
from bot.middlewares.db_user import DbUserMiddleware

# Routers
from bot.handlers import user, payments, admin

# Background tasks
from bot.api.server import start_api_server
from bot.services import cryptopay, yoomoney, platega

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _can_connect(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.5)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


async def maybe_start_mock_vpn_api() -> asyncio.subprocess.Process | None:
    if not settings.start_mock_vpn_api:
        return None

    parsed = urlparse(settings.vpn_api_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    if host not in {"127.0.0.1", "localhost"}:
        logger.info("Mock VPN API autostart skipped: vpn_api_url is not local")
        return None
    if _can_connect(host, port):
        logger.info("Mock VPN API autostart skipped: %s:%s is already listening", host, port)
        return None

    script = Path(__file__).resolve().parent.parent / "mock_vpn_api.py"
    if not script.exists():
        logger.warning("Mock VPN API script not found: %s", script)
        return None

    logger.info("Starting mock VPN API on %s:%s", host, port)
    return await asyncio.create_subprocess_exec(sys.executable, str(script))

async def setup_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Запустить бота"),
    ]
    await bot.set_my_commands(commands)

async def check_payments_task(bot: Bot):
    while True:
        try:
            # Placeholder for actual payment checking logic (e.g. cryptopay active invoices)
            # Webhooks are used for YooMoney and Platega, so periodic check is mainly for CryptoBot if webhooks aren't used.
            pass
        except Exception as e:
            logger.error(f"Error checking payments: {e}")
        await asyncio.sleep(15)

async def check_subscriptions_task(bot: Bot):
    while True:
        try:
            # Check expiring subscriptions logic
            pass
        except Exception as e:
            logger.error(f"Error checking subscriptions: {e}")
        await asyncio.sleep(15 * 60)

async def main():
    logger.info("Initializing DB...")
    await crud.init_db()

    logger.info("Loading dynamic settings...")
    # settings are loaded dynamically where needed via crud.get_setting

    session = AiohttpSession(proxy=settings.proxy_url) if settings.proxy_url else None
    from aiogram.client.default import DefaultBotProperties
    bot = Bot(token=settings.bot_token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()

    dp.message.middleware(DbUserMiddleware())
    dp.callback_query.middleware(DbUserMiddleware())

    dp.include_router(user.router)
    dp.include_router(payments.router)
    dp.include_router(admin.router)

    from bot.api.routes import user as api_user, admin as api_admin, webhooks
    api_user.set_bot(bot)
    api_admin.set_bot(bot)
    webhooks.set_bot(bot)

    await setup_bot_commands(bot)

    mock_vpn_process = await maybe_start_mock_vpn_api()

    logger.info("Starting background tasks...")
    asyncio.create_task(check_payments_task(bot))
    asyncio.create_task(check_subscriptions_task(bot))
    asyncio.create_task(start_api_server())
    logger.info("Bot is starting...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        if mock_vpn_process is not None and mock_vpn_process.returncode is None:
            mock_vpn_process.terminate()
            await mock_vpn_process.wait()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
