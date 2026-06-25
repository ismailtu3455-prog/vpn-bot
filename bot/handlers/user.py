from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.config import settings, db_settings
from bot.database import crud
from bot.database.models import User
from bot.keyboards.inline import (
    main_menu, back_kb, no_client_kb, client_kb, mandatory_sub_kb,
    plans_kb, back_to_buy_kb, share_gift_kb,
)
from bot.services.delivery import deliver_vpn
from bot.states import UserState, WithdrawalState
from bot.texts import Texts

logger = logging.getLogger(__name__)
router = Router()


async def is_admin(user_id: int) -> bool:
    """Check if user is admin (in config or DB)."""
    if user_id in settings.get_admin_ids:
        return True
    admins = await crud.get_admins()
    return any(a.user_id == user_id for a in admins)


async def _check_mandatory_sub(bot, user_id: int) -> bool:
    """Returns True if user is subscribed to mandatory channel or no channel set."""
    channel_id = db_settings.get("main_channel_id")
    if not channel_id:
        return True
    try:
        member = await bot.get_chat_member(int(channel_id), user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return True


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot) -> None:
    await state.clear()
    user_id = message.from_user.id
    args = message.text.split(" ", 1)[1].strip() if len(message.text.split()) > 1 else ""

    user = await crud.get_user(user_id)
    if user is None:
        return

    # Handle gift card
    if args.startswith("gift_"):
        code = args[5:]
        gc = await crud.get_gift_card(code)
        
        if not gc or gc.is_used:
            await message.answer("❌ Подарочная карта не найдена или уже использована.")
            return

        if gc.buyer_user_id == user_id:
            from aiogram.types import InlineKeyboardButton
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            kb = InlineKeyboardBuilder()
            kb.row(
                InlineKeyboardButton(text="✅ Да, активировать", callback_data=f"confirm_gift:{code}"),
                InlineKeyboardButton(text="❌ Нет, отменить", callback_data="cancel_gift")
            )
            await message.answer(
                "❓ <b>Вы уверены, что хотите активировать свою же подарочную карту?</b>\n"
                "Вы купили её в подарок кому-то другому. Если активируете, она продлит ваш VPN.",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return

        # Not their own card, redeem immediately
        gc = await crud.use_gift_card(code, user_id)
        if gc:
            bot_obj = bot
            await deliver_vpn(bot_obj, user_id, gc.days, is_gift=True)
            await message.answer(
                f"🎁 <b>Подарочная карта активирована!</b>\nВам выданы <b>{gc.days} дней</b> VPN.",
                parse_mode="HTML",
            )
            return
        else:
            await message.answer("❌ Подарочная карта не найдена или уже использована.")
            return

    # Handle referral
    ref_reward = int(db_settings.get("ref_reward_start") or "50")
    if args.startswith("ref_") and args[4:].isdigit():
        ref_id = int(args[4:])
        if ref_id != user_id and user.ref_id is None:
            # First time referral
            await crud.update_user_ref_id(user_id, ref_id)
            await crud.add_user_balance(ref_id, ref_reward)
            await message.bot.send_message(
                ref_id,
                f"🎉 По вашей реферальной ссылке зарегистрировался новый пользователь!\n"
                f"💰 Вам начислено <b>{ref_reward}₽</b> на баланс.",
                parse_mode="HTML",
            )

    # Check mandatory subscription
    channel_id = db_settings.get("main_channel_id")
    if channel_id:
        subscribed = await _check_mandatory_sub(bot, user_id)
        if not subscribed:
            channel_url = db_settings.get("main_channel_url") or ""
            if channel_url:
                await message.answer(
                    "📢 Для использования бота необходимо подписаться на наш канал!",
                    reply_markup=mandatory_sub_kb(channel_url),
                )
                return

    admin = await is_admin(user_id)
    name = message.from_user.first_name or "Гость"
    await message.answer(
        Texts.START.format(name=name),
        reply_markup=main_menu(is_admin=admin),
        parse_mode="HTML",
    )


# ─── Back / main menu ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "back")
async def cb_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_id = call.from_user.id
    admin = await is_admin(user_id)
    name = call.from_user.first_name or "Гость"
    await call.message.edit_text(
        Texts.START.format(name=name),
        reply_markup=main_menu(is_admin=admin),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Help ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery) -> None:
    await call.message.edit_text(
        Texts.HELP,
        reply_markup=back_kb("back"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await call.answer()


# ─── Mandatory sub ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "check_mandatory_sub")
async def cb_check_mandatory_sub(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    bot = call.bot
    subscribed = await _check_mandatory_sub(bot, user_id)
    if subscribed:
        admin = await is_admin(user_id)
        name = call.from_user.first_name or "Гость"
        await call.message.edit_text(
            Texts.START.format(name=name),
            reply_markup=main_menu(is_admin=admin),
            parse_mode="HTML",
        )
        await call.answer("✅ Подписка подтверждена!")
    else:
        await call.answer("❌ Вы ещё не подписаны на канал!", show_alert=True)


# ─── My VPN ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "my")
async def cb_my(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    user = await crud.get_user(user_id)
    if user is None:
        await call.answer("Ошибка", show_alert=True)
        return

    if not user.vpn_name:
        await call.message.edit_text(
            Texts.NO_VPN,
            reply_markup=no_client_kb(user.balance or 0.0),
            parse_mode="HTML",
        )
        await call.answer()
        return

    from bot.services import vpn as vpn_service
    try:
        data = await vpn_service.get_client(user.vpn_name)
        client = vpn_service.normalize_client_payload(data)
        left_days = client.get("left_days", "?")
        expires_at = client.get("expires_at", "?")
        used = round((client.get("used_traffic_bytes") or 0) / 1_073_741_824, 2)
        limit = client.get("traffic_limit_gb") or 0
        is_banned = client.get("is_banned", False)

        sub_url = f"https://{settings.subscription_domain}/sub/{user.sub_token}"
        traffic_line = f"📊 {used} ГБ / {'∞' if limit == 0 else f'{limit} ГБ'}"
        ban_line = "\n🚫 <b>VPN заблокирован!</b>" if is_banned else ""

        text = (
            f"👤 <b>Мой VPN</b>\n\n"
            f"🔑 Профиль: <code>{user.vpn_name}</code>\n"
            f"⏳ Осталось: <b>{left_days} дн.</b>\n"
            f"📅 Истекает: <b>{expires_at}</b>\n"
            f"{traffic_line}{ban_line}\n\n"
            f"💰 Баланс: <b>{user.balance:.2f}₽</b>\n\n"
            f"🔗 Ссылка-подписка:\n<code>{sub_url}</code>"
        )
        await call.message.edit_text(
            text,
            reply_markup=client_kb(user.balance or 0.0),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"cb_my VPN error for {user_id}: {e}")
        await call.message.edit_text(
            f"👤 <b>Мой VPN</b>\n\n"
            f"🔑 Профиль: <code>{user.vpn_name}</code>\n"
            f"⚠️ Не удалось получить данные с сервера\n\n"
            f"💰 Баланс: <b>{user.balance:.2f}₽</b>",
            reply_markup=client_kb(user.balance or 0.0),
            parse_mode="HTML",
        )
    await call.answer()


# ─── Buy ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery) -> None:
    plans = await crud.get_all_plans()
    if not plans:
        await call.answer("Тарифы не настроены", show_alert=True)
        return
    user = await crud.get_user(call.from_user.id)
    has_promo = bool(user and user.promo_used)
    await call.message.edit_text(
        Texts.CHOOSE_PLAN,
        reply_markup=plans_kb(plans, has_promo=has_promo),
        parse_mode="HTML",
    )
    await call.answer()

@router.callback_query(F.data == "buy_gift")
async def cb_buy_gift(call: CallbackQuery) -> None:
    plans = await crud.get_all_plans()
    if not plans:
        await call.answer("Тарифы не настроены", show_alert=True)
        return
    await call.message.edit_text(
        "🎁 <b>Купить подарочную карту</b>\n\nВыберите тариф для подарка:",
        reply_markup=plans_kb(plans, prefix="giftplan"),
        parse_mode="HTML",
    )
    await call.answer()

@router.callback_query(F.data.startswith("send_gift_email:"))
async def cb_send_gift_email(call: CallbackQuery, state: FSMContext) -> None:
    gift_code = call.data.split(":", 1)[1]
    await state.update_data(gift_code=gift_code)
    await state.set_state(UserState.wait_for_gift_email)
    await call.message.edit_text(
        "📧 <b>Отправка подарка на Email</b>\n\n"
        "Введите Email адрес получателя:",
        reply_markup=back_kb("my"),
        parse_mode="HTML"
    )
    await call.answer()

@router.message(UserState.wait_for_gift_email)
async def process_gift_email(message: Message, state: FSMContext) -> None:
    email = message.text.strip().lower()
    if "@" not in email or "." not in email:
        await message.answer("❌ Некорректный Email. Попробуйте еще раз:", reply_markup=back_kb("my"))
        return
        
    data = await state.get_data()
    gift_code = data.get("gift_code")
    if not gift_code:
        await state.clear()
        await message.answer("❌ Ошибка. Начните заново.")
        return
        
    bot_info = await message.bot.get_me()
    gift_link = f"https://t.me/{bot_info.username}?start=gift_{gift_code}"
    
    from bot.services.email import send_gift_email

    if await send_gift_email(email, gift_link):
        await message.answer(f"✅ Подарок успешно отправлен на <b>{email}</b>!", parse_mode="HTML", reply_markup=back_kb("my"))
        await state.clear()
    else:
        await message.answer("❌ Ошибка отправки Email.", reply_markup=back_kb("my"))


# ─── Reissue key ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "reissue_key")
async def cb_reissue_key(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    user = await crud.get_user(user_id)
    if not user or not user.vpn_name:
        await call.answer("VPN не найден", show_alert=True)
        return
    from bot.config import settings, db_settings
    from bot.keyboards.inline import reissue_payment_methods_kb
    
    is_admin = call.from_user.id in settings.get_admin_ids
    is_free_available = is_admin or not user.free_reissue_used
    
    cost = 50.0
    text = (
        f"🔄 <b>Переиздание ключа</b>\n\n"
        f"Старый ключ перестанет работать.\n"
    )
    if is_free_available:
        text += f"Вам доступен <b>1 бесплатный</b> перевыпуск ключа.\n\nВыберите действие:"
    else:
        text += f"Стоимость перевыпуска ключа: <b>{cost}₽</b>.\n\nВыберите способ оплаты:"

    await call.message.edit_text(
        text,
        reply_markup=reissue_payment_methods_kb(user.balance, cost, db_settings, is_free_available),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "pay_reissue_free")
async def cb_pay_reissue_free(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    user = await crud.get_user(user_id)
    if not user or not user.vpn_name:
        await call.answer("VPN не найден", show_alert=True)
        return
    from bot.config import settings
    is_admin = user_id in settings.get_admin_ids
    if not is_admin and user.free_reissue_used:
        await call.answer("Бесплатный перевыпуск уже использован", show_alert=True)
        return
    
    success = await _do_reissue(call, user_id, user.vpn_name)
    if success and not is_admin:
        from bot.database.models import User
        from bot.database.crud import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            u = await session.get(User, user_id)
            if u:
                u.free_reissue_used = True
                await session.commit()


async def _do_reissue(call: CallbackQuery | Message | None, user_id: int, old_name: str) -> bool:
    from bot.services import vpn as vpn_service
    if not old_name:
        if call is not None:
            target_message = call.message if isinstance(call, CallbackQuery) else call
            await target_message.answer("❌ VPN не найден.")
        return False
    try:
        await vpn_service.reissue_client_same_name(
            old_name,
            default_days=30,
            default_limit_gb=int(db_settings.get("default_limit_gb") or "0"),
            device_limit=7,
        )
        await crud.regenerate_sub_token(user_id)
        user = await crud.get_user(user_id)
        sub_url = f"https://{settings.subscription_domain}/sub/{user.sub_token}" if user else ""
        if call is not None:
            text = (
                f"✅ <b>Ключ переиздан!</b>\n\n"
                f"🔑 Профиль: <code>{old_name}</code>\n"
                f"🔗 Ссылка-подписка:\n<code>{sub_url}</code>"
            )
            if isinstance(call, CallbackQuery):
                await call.message.edit_text(text, reply_markup=back_kb("my"), parse_mode="HTML")
            else:
                await call.answer(text, reply_markup=back_kb("my"), parse_mode="HTML")
        return True
    except Exception as e:
        if call is not None:
            target_message = call.message if isinstance(call, CallbackQuery) else call
            await target_message.answer(f"❌ Ошибка переиздания: {e}", reply_markup=back_kb("my"))
        return False
    finally:
        if isinstance(call, CallbackQuery):
            await call.answer()


# ─── Promo ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "enter_promo")
async def cb_enter_promo(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserState.wait_for_promo)
    await call.message.edit_text(
        "🎁 Введите промокод:",
        reply_markup=back_kb("back"),
    )
    await call.answer()


@router.message(UserState.wait_for_promo)
async def process_promo(message: Message, state: FSMContext) -> None:
    await state.clear()
    code = message.text.strip().upper()
    user_id = message.from_user.id
    await _apply_promo(message, user_id, code)


async def _apply_promo(message: Message, user_id: int, code: str) -> None:
    user = await crud.get_user(user_id)
    if user and user.promo_used:
        await message.answer(Texts.PROMO_ALREADY_USED, reply_markup=back_kb("back"))
        return
    promo = await crud.get_promocode(code)
    if not promo or not promo.is_active:
        await message.answer(Texts.PROMO_INVALID, reply_markup=back_kb("back"))
        return
    if promo.uses_count >= promo.max_uses:
        await message.answer(Texts.PROMO_INVALID, reply_markup=back_kb("back"))
        return

    await crud.increment_promo_uses(code)
    await crud.update_user_promo(user_id, code)

    if promo.promo_type == "days":
        reward = f"{int(promo.value)} дней VPN"
        await deliver_vpn(message.bot, user_id, int(promo.value))
    elif promo.promo_type == "balance":
        reward = f"{promo.value}₽ на баланс"
        await crud.add_user_balance(user_id, promo.value)
    elif promo.promo_type == "discount":
        reward = f"Скидка {promo.value}% на следующую покупку"
    else:
        reward = str(promo.value)

    await message.answer(
        Texts.PROMO_APPLIED.format(reward=reward),
        reply_markup=back_kb("back"),
        parse_mode="HTML",
    )


# ─── Test period ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "take_test")
async def cb_take_test(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    test_enabled = db_settings.get("test_enabled", "1") == "1"
    if not test_enabled:
        await call.answer("Тест-период сейчас недоступен", show_alert=True)
        return
    user = await crud.get_user(user_id)
    if not user:
        await call.answer("Ошибка", show_alert=True)
        return
    if user.test_taken:
        await call.answer("Вы уже использовали тест-период", show_alert=True)
        return
    test_days = int(db_settings.get("test_days") or "3")
    await crud.update_user_test_taken(user_id)
    success = await deliver_vpn(call.bot, user_id, test_days)
    if success:
        await call.message.edit_text(
            f"🧪 <b>Тест-период активирован!</b>\n\n"
            f"Вам выданы <b>{test_days} дней</b> VPN бесплатно.\n"
            f"Проверьте раздел «Мой VPN».",
            reply_markup=back_kb("my"),
            parse_mode="HTML",
        )
    else:
        await call.answer("Ошибка активации тест-периода", show_alert=True)
    await call.answer()


# ─── Referrals ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "referrals")
async def cb_referrals(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    user = await crud.get_user(user_id)
    if not user:
        await call.answer("Ошибка", show_alert=True)
        return
    refs_count = await crud.get_referrals_count(user_id)
    bot_info = await call.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    await call.message.edit_text(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей и получайте бонусы!\n\n"
        f"👤 Приглашено: <b>{refs_count}</b>\n"
        f"💰 Заработано: <b>{user.ref_earned:.2f}₽</b>\n\n"
        f"🔗 Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        f"💡 За каждого нового пользователя по вашей ссылке вы получаете "
        f"<b>{db_settings.get('ref_reward_start', '50')}₽</b>",
        reply_markup=back_kb("back"),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Support ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "support")
async def cb_support(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserState.wait_for_support)
    await call.message.edit_text(
        "💬 <b>Поддержка</b>\n\nОпишите вашу проблему или вопрос:",
        reply_markup=back_kb("back"),
        parse_mode="HTML",
    )
    await call.answer()


@router.message(UserState.wait_for_support)
async def process_support(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    user = await crud.get_user(user_id)
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID:{user_id}"
    support_text = (message.text or "").strip()
    if not support_text:
        await message.answer("❌ Сообщение пустое. Опишите проблему текстом.")
        return
    await state.clear()

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"💬 Ответить {username}",
            callback_data=f"support_reply:{user_id}",
        )
    ]])
    support_payload = (
        f"📩 <b>Обращение в поддержку</b>\n\n"
        f"👤 {html.escape(username)}\n"
        f"🆔 <code>{user_id}</code>\n\n"
        f"💬 {html.escape(support_text)}"
    )

    recipients: set[int] = set()
    payment_channel_id = db_settings.get("payment_channel_id")
    if payment_channel_id:
        try:
            recipients.add(int(payment_channel_id))
        except ValueError:
            logger.error("Invalid payment_channel_id for support: %s", payment_channel_id)

    recipients.update(settings.get_admin_ids)
    for admin in await crud.get_admins():
        recipients.add(admin.user_id)

    if not recipients:
        await message.answer(
            "❌ Поддержка сейчас не настроена. Сначала укажи админа или канал для обращений.",
            reply_markup=back_kb("back"),
        )
        return

    delivered = False
    for recipient_id in recipients:
        try:
            await message.bot.send_message(
                int(recipient_id),
                support_payload,
                reply_markup=reply_kb,
                parse_mode="HTML",
            )
            delivered = True
        except Exception as e:
            logger.error("Support forward error to %s: %s", recipient_id, e)

    if not delivered:
        await message.answer(
            "❌ Не удалось отправить обращение администраторам. Попробуйте позже.",
            reply_markup=back_kb("back"),
        )
        return

    await message.answer(
        "✅ <b>Обращение отправлено!</b>\nМы ответим вам в ближайшее время.",
        reply_markup=back_kb("back"),
        parse_mode="HTML",
    )


# ─── Withdraw ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "withdraw")
async def cb_withdraw(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    user = await crud.get_user(user_id)
    if not user or (user.ref_earned or 0.0) < 100:
        await call.answer("Минимальная сумма вывода 100₽", show_alert=True)
        return
    await state.set_state(WithdrawalState.wait_for_details)
    await call.message.edit_text(
        f"💵 <b>Вывод средств</b>\n\n"
        f"Ваш баланс: <b>{user.ref_earned:.2f}₽</b>\n\n"
        f"Укажите реквизиты для вывода (номер карты, телефон или кошелёк):",
        reply_markup=back_kb("back"),
        parse_mode="HTML",
    )
    await call.answer()


@router.message(WithdrawalState.wait_for_details)
async def process_withdraw(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    user = await crud.get_user(user_id)
    if not user:
        return
    amount = user.ref_earned or 0.0
    wr = await crud.create_withdrawal(user_id, amount, message.text)

    payment_channel_id = db_settings.get("payment_channel_id")
    if payment_channel_id:
        from bot.keyboards.inline import withdrawal_decision_kb
        try:
            await message.bot.send_message(
                int(payment_channel_id),
                f"💵 <b>Заявка на вывод #{wr.id}</b>\n\n"
                f"👤 {message.from_user.username or user_id}\n"
                f"💰 Сумма: <b>{amount:.2f}₽</b>\n"
                f"📝 Реквизиты: {message.text}",
                reply_markup=withdrawal_decision_kb(wr.id),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Withdrawal notification error: {e}")

    await message.answer(
        f"✅ <b>Заявка #{wr.id} принята!</b>\nОбработаем в течение 24 часов.",
        reply_markup=back_kb("back"),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("confirm_gift:"))
async def cb_confirm_gift(call: CallbackQuery) -> None:
    code = call.data.split(":")[1]
    user_id = call.from_user.id
    
    gc = await crud.use_gift_card(code, user_id)
    if gc:
        await deliver_vpn(call.bot, user_id, gc.days, is_gift=True)
        await call.message.edit_text(
            f"🎁 <b>Подарочная карта активирована!</b>\nВам выданы <b>{gc.days} дней</b> VPN.",
            parse_mode="HTML",
        )
    else:
        await call.message.edit_text("❌ Подарочная карта не найдена или уже использована.")
    await call.answer()

@router.callback_query(F.data == "cancel_gift")
async def cb_cancel_gift(call: CallbackQuery) -> None:
    await call.message.edit_text("❌ Активация подарочной карты отменена.")
    await call.answer()
