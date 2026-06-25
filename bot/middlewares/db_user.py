from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from bot.database.crud import register_user


class DbUserMiddleware(BaseMiddleware):
    """Register every interacting user in the database."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        ref_id: int | None = None

        if isinstance(event, Message) and event.from_user:
            tg_user = event.from_user
            # Try to extract ref from /start payload
            if event.text and event.text.startswith("/start "):
                payload = event.text.split(" ", 1)[1].strip()
                if payload.startswith("ref_") and payload[4:].isdigit():
                    ref_id = int(payload[4:])
            user = await register_user(
                user_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                language_code=tg_user.language_code,
                ref_id=ref_id,
            )
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_user = event.from_user
            user = await register_user(
                user_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                language_code=tg_user.language_code,
            )

        if user is not None:
            data["db_user"] = user

        return await handler(event, data)
