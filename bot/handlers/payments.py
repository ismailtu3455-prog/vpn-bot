from __future__ import annotations

import html
import logging
import uuid
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, Message, LabeledPrice, PreCheckoutQuery,
    SuccessfulPayment, InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot.config import settings, db_settings
from bot.database import crud
from bot.keyboards.inline import (
    back_kb, payment_methods_kb, invoice_kb, yoomoney_invoice_kb,
)
from bot.services import cryptopay, platega, yoomoney
from bot.services.delivery import deliver_vpn
from bot.states import UserState
from bot.texts import Texts

logger = logging.getLogger(__name__)
router = Router()


async def _notify_yoomoney_manual_review(bot, inv) -> None:
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"✅ Подтвердить {inv.invoice_id}",
        callback_data=f"adm:inv_approve_by_ext:{inv.invoice_id}",
    ))
    payer_name = html.escape(inv.payer_name or "не указано")
    text = (
        f"💛 <b>YooMoney: нужна ручная проверка</b>\n\n"
        f"👤 Пользователь: <code>{inv.user_id}</code>\n"
        f"📦 Тариф: <b>{inv.plan_title}</b>\n"
        f"💰 Сумма: <b>{inv.amount_rub:.0f}₽</b>\n"
        f"🧾 ФИО отправителя: <b>{payer_name}</b>\n"
        f"🔑 Invoice ID: <code>{inv.invoice_id}</code>\n"
        f"🏷 Label: <code>{inv.label or '—'}</code>"
    )

    recipient_ids = set(settings.get_admin_ids)
    for admin in await crud.get_admins():
        recipient_ids.add(admin.user_id)

    for recipient_id in recipient_ids:
        try:
            await bot.send_message(
                int(recipient_id),
                text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("YooMoney manual review notify admin %s error: %s", recipient_id, e)

    pay_ch = db_settings.get("payment_channel_id")
    if pay_ch:
        try:
            await bot.send_message(
                int(pay_ch),
                text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("YooMoney manual review notify channel error: %s", e)

async def get_plan_or_reissue(plan_id: str):
    if plan_id == "reissue":
        from bot.database.models import Plan
        return Plan(id="reissue", title="Перевыпуск ключа", price=50.0, days=0)
    from bot.database import crud
    return await crud.get_plan(plan_id)



# ─── Plan selection ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("plan:"))
async def cb_plan(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 1)[1]
    await _show_payment_methods(call, plan_id, is_gift=False)


@router.callback_query(F.data.startswith("giftplan:"))
async def cb_giftplan(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 1)[1]
    await _show_payment_methods(call, plan_id, is_gift=True)


async def _show_payment_methods(call: CallbackQuery, plan_id: str, is_gift: bool) -> None:
    plan = await get_plan_or_reissue(plan_id)
    if not plan:
        await call.answer("Тариф не найден", show_alert=True)
        return
    user = await crud.get_user(call.from_user.id)
    balance = user.balance if user else 0.0
    price = plan.price

    # Check promo discount
    promo_discount = 0.0
    if user and user.promo_used:
        promo = await crud.get_promocode(user.promo_used)
        if promo and promo.promo_type == "discount":
            promo_discount = round(price * promo.value / 100, 2)
            price = round(price - promo_discount, 2)

    gift_label = "🎁 " if is_gift else ""
    await call.message.edit_text(
        Texts.CHOOSE_PAYMENT.format(
            title=f"{gift_label}{plan.title}",
            price=f"{price:.0f}",
        ),
        reply_markup=payment_methods_kb(plan, balance, price, is_gift, db_settings),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Balance payment ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:balance:"))
async def cb_pay_balance(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_balance_payment(call, plan_id, is_gift=False)


@router.callback_query(F.data.startswith("giftpay:balance:"))
async def cb_giftpay_balance(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_balance_payment(call, plan_id, is_gift=True)


async def _process_balance_payment(call: CallbackQuery, plan_id: str, is_gift: bool) -> None:
    user_id = call.from_user.id
    plan = await get_plan_or_reissue(plan_id)
    if not plan:
        await call.answer("Тариф не найден", show_alert=True)
        return
    user = await crud.get_user(user_id)
    if not user or (user.balance or 0.0) < plan.price:
        await call.answer("Недостаточно средств на балансе", show_alert=True)
        return
    await crud.add_user_balance(user_id, -plan.price)
    invoice_id = f"bal_{uuid.uuid4().hex[:12]}"
    await crud.create_invoice(
        user_id=user_id,
        plan_key=plan.id,
        plan_title=plan.title,
        days=plan.days,
        amount_rub=plan.price,
        gateway="balance",
        invoice_id=invoice_id,
        is_gift=is_gift,
    )
    await crud.update_invoice_status(invoice_id, "paid")
    if plan.id == "reissue":
        from bot.handlers.user import _do_reissue
        success = await _do_reissue(call, user_id, user.vpn_name)
        if not success:
            await crud.add_user_balance(user_id, plan.price)
            await call.answer("Ошибка перевыпуска, деньги возвращены на баланс", show_alert=True)
        await call.answer()
        return

    recipient_id = user_id
    if is_gift:
        gc = await crud.create_gift_card(user_id, plan.days)
        bot_info = await call.bot.get_me()
        gift_link = f"https://t.me/{bot_info.username}?start=gift_{gc.code}"
        from bot.keyboards.inline import gift_sent_kb
        await call.message.edit_text(
            f"✅ <b>Оплата балансом прошла успешно!</b>\n\n"
            f"🎁 <b>Ваш подарочный ключ на {plan.days} дней:</b>\n"
            f"<code>{gift_link}</code>\n\n"
            f"Отправьте эту ссылку тому, кому хотите подарить VPN, или отправьте на Email.",
            reply_markup=gift_sent_kb(gc.code),
            parse_mode="HTML",
        )
    else:
        success = await deliver_vpn(call.bot, recipient_id, plan.days, is_gift=False)
        if success:
            await call.message.edit_text(
                f"✅ Оплата балансом прошла успешно!\n"
                f"📦 Тариф: {plan.title}\n"
                f"💰 Списано: {plan.price}₽",
                reply_markup=back_kb("my"),
                parse_mode="HTML",
            )
        else:
            await call.answer("Ошибка выдачи VPN, обратитесь в поддержку", show_alert=True)
    await call.answer()


# ─── CryptoBot payment ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:crypto:"))
async def cb_pay_crypto(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_crypto_payment(call, plan_id, is_gift=False)


@router.callback_query(F.data.startswith("giftpay:crypto:"))
async def cb_giftpay_crypto(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_crypto_payment(call, plan_id, is_gift=True)


async def _process_crypto_payment(call: CallbackQuery, plan_id: str, is_gift: bool) -> None:
    user_id = call.from_user.id
    plan = await get_plan_or_reissue(plan_id)
    if not plan:
        await call.answer("Тариф не найден", show_alert=True)
        return
    payload = f"user:{user_id}:plan:{plan.id}:gift:{int(is_gift)}"
    result = await cryptopay.create_crypto_invoice(
        amount_rub=plan.price,
        description=f"Adoria VPN — {plan.title}",
        payload=payload,
    )
    if not result:
        await call.answer("CryptoBot недоступен", show_alert=True)
        return
    crypto_invoice_id, pay_url = result
    usdt_rate = float(db_settings.get("usdt_rate") or "90")
    amount_usdt = round(plan.price / usdt_rate, 2)
    inv = await crud.create_invoice(
        user_id=user_id,
        plan_key=plan.id,
        plan_title=plan.title,
        days=plan.days,
        amount_rub=plan.price,
        amount_currency=amount_usdt,
        currency=settings.crypto_currency,
        gateway="cryptopay",
        invoice_id=crypto_invoice_id,
        is_gift=is_gift,
    )
    await call.message.edit_text(
        Texts.INVOICE_CREATED.format(
            title=plan.title,
            gateway="CryptoBot",
            amount=amount_usdt,
            currency=settings.crypto_currency,
        ),
        reply_markup=invoice_kb(pay_url, crypto_invoice_id),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Stars payment ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:stars:"))
async def cb_pay_stars(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_stars_payment(call, plan_id, is_gift=False)


@router.callback_query(F.data.startswith("giftpay:stars:"))
async def cb_giftpay_stars(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_stars_payment(call, plan_id, is_gift=True)


async def _process_stars_payment(call: CallbackQuery, plan_id: str, is_gift: bool) -> None:
    plan = await get_plan_or_reissue(plan_id)
    if not plan:
        await call.answer("Тариф не найден", show_alert=True)
        return
    stars_rate = float(db_settings.get("stars_rate") or settings.stars_rate or "1.35")
    stars_amount = max(1, int(plan.price / stars_rate))
    payload = f"stars:user:{call.from_user.id}:plan:{plan.id}:gift:{int(is_gift)}"
    await call.message.answer_invoice(
        title=f"Adoria VPN — {plan.title}",
        description=f"VPN подписка на {plan.days} дней",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=plan.title, amount=stars_amount)],
    )
    await call.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    payment: SuccessfulPayment = message.successful_payment
    payload = payment.invoice_payload
    user_id = message.from_user.id

    # Parse payload: "stars:user:{uid}:plan:{plan_id}:gift:{0|1}"
    try:
        parts = payload.split(":")
        plan_id = parts[parts.index("plan") + 1]
        is_gift = "gift" in parts and parts[parts.index("gift") + 1] == "1"
    except Exception:
        logger.error(f"Stars: bad payload {payload}")
        return

    plan = await get_plan_or_reissue(plan_id)
    if not plan:
        await message.answer("❌ Тариф не найден. Обратитесь в поддержку.")
        return

    stars_amount = payment.total_amount
    invoice_id = f"stars_{payment.telegram_payment_charge_id}"
    await crud.create_invoice(
        user_id=user_id,
        plan_key=plan.id,
        plan_title=plan.title,
        days=plan.days,
        amount_rub=plan.price,
        amount_currency=stars_amount,
        currency="XTR",
        gateway="stars",
        invoice_id=invoice_id,
        is_gift=is_gift,
    )
    await crud.update_invoice_status(invoice_id, "paid")
    
    if plan.id == "reissue":
        from bot.handlers.user import _do_reissue
        user = await crud.get_user(user_id)
        if user:
            await _do_reissue(message, user_id, user.vpn_name)
        await message.answer("✅ Оплата Stars прошла успешно!\nКлюч перевыпущен.")
    elif is_gift:
        gc = await crud.create_gift_card(user_id, plan.days)
        bot_info = await message.bot.get_me()
        gift_link = f"https://t.me/{bot_info.username}?start=gift_{gc.code}"
        from bot.keyboards.inline import gift_sent_kb
        await message.answer(
            f"✅ <b>Оплата Stars прошла успешно!</b>\n\n"
            f"🎁 <b>Ваш подарочный ключ на {plan.days} дней:</b>\n"
            f"<code>{gift_link}</code>\n\n"
            f"Отправьте эту ссылку тому, кому хотите подарить VPN, или отправьте на Email.",
            reply_markup=gift_sent_kb(gc.code),
            parse_mode="HTML",
        )
    else:
        await deliver_vpn(message.bot, user_id, plan.days)

# ─── Tome (manual SBP) payment ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:tome:"))
async def cb_pay_tome(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_tome_payment(call, plan_id, is_gift=False)


@router.callback_query(F.data.startswith("giftpay:tome:"))
async def cb_giftpay_tome(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_tome_payment(call, plan_id, is_gift=True)


async def _process_tome_payment(call: CallbackQuery, plan_id: str, is_gift: bool) -> None:
    plan = await get_plan_or_reissue(plan_id)
    if not plan:
        await call.answer("Тариф не найден", show_alert=True)
        return
    tome_phone = db_settings.get("tome_phone") or "не настроен"
    tome_bank = db_settings.get("tome_bank") or ""
    invoice_id = f"sbp_{uuid.uuid4().hex[:10]}"
    await crud.create_invoice(
        user_id=call.from_user.id,
        plan_key=plan.id,
        plan_title=plan.title,
        days=plan.days,
        amount_rub=plan.price,
        gateway="tome",
        invoice_id=invoice_id,
        is_gift=is_gift,
    )

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text=f"✅ Подтвердить #{invoice_id}",
        callback_data=f"adm:inv_approve_by_ext:{invoice_id}",
    ))
    notify_text = (
        f"💳 <b>Новый платёж (СБП)</b>\n\n"
        f"👤 {call.from_user.id} (@{call.from_user.username or '—'})\n"
        f"📦 {'Подарок: ' if is_gift else ''}{plan.title}\n"
        f"💰 {plan.price}₽\n"
        f"🔑 <code>{invoice_id}</code>"
    )

    recipient_ids = set(settings.get_admin_ids)
    for admin in await crud.get_admins():
        recipient_ids.add(admin.user_id)
    for admin_id in recipient_ids:
        try:
            await call.bot.send_message(
                int(admin_id),
                notify_text,
                reply_markup=kb.as_markup(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"SBP notify admin {admin_id} error: {e}")

    pay_ch = db_settings.get("payment_channel_id")
    if pay_ch:
        try:
            await call.bot.send_message(
                int(pay_ch),
                notify_text,
                reply_markup=kb.as_markup(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"SBP notify channel error: {e}")

    await call.message.edit_text(
        f"📱 <b>Оплата через СБП</b>\n\n"
        f"📦 Тариф: <b>{plan.title}</b>\n"
        f"💰 Сумма: <b>{plan.price}₽</b>\n\n"
        f"Переведите <b>{plan.price}₽</b> по СБП:\n"
        f"📞 <code>{tome_phone}</code>\n"
        f"🏦 {tome_bank}\n\n"
        f"В комментарии укажите: <code>{invoice_id}</code>\n\n"
        f"✅ После перевода администратор подтвердит платёж вручную.",
        reply_markup=back_kb("buy"),
        parse_mode="HTML",
    )
    await call.answer()


# ─── YooMoney payment ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:yoomoney:"))
async def cb_pay_yoomoney(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_yoomoney_payment(call, plan_id, is_gift=False)


@router.callback_query(F.data.startswith("giftpay:yoomoney:"))
async def cb_giftpay_yoomoney(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_yoomoney_payment(call, plan_id, is_gift=True)


async def _process_yoomoney_payment(call: CallbackQuery, plan_id: str, is_gift: bool) -> None:
    wallet = db_settings.get("yoomoney_wallet")
    if not wallet:
        await call.answer("YooMoney не настроен", show_alert=True)
        return
    plan = await get_plan_or_reissue(plan_id)
    if not plan:
        await call.answer("Тариф не найден", show_alert=True)
        return
    user_id = call.from_user.id
    invoice_id = f"ym_{uuid.uuid4().hex[:8]}"
    label = f"{user_id}:{plan.id}:{invoice_id}"
    pay_url = yoomoney.generate_payment_link(
        wallet=wallet,
        amount=plan.price,
        label=label,
        desc=f"Adoria VPN — {plan.title}",
        payment_type="AC",
    )
    await crud.create_invoice(
        user_id=user_id,
        plan_key=plan.id,
        plan_title=plan.title,
        days=plan.days,
        amount_rub=plan.price,
        currency="RUB",
        gateway="yoomoney",
        invoice_id=invoice_id,
        label=label,
        is_gift=is_gift,
    )
    await call.message.edit_text(
        Texts.YOOMONEY_INVOICE.format(title=plan.title, amount=f"{plan.price:.0f}"),
        reply_markup=yoomoney_invoice_kb(pay_url, invoice_id),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Platega payment ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:platega:"))
async def cb_pay_platega(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_platega_payment(call, plan_id, is_gift=False)


@router.callback_query(F.data.startswith("giftpay:platega:"))
async def cb_giftpay_platega(call: CallbackQuery) -> None:
    plan_id = call.data.split(":", 2)[2]
    await _process_platega_payment(call, plan_id, is_gift=True)


async def _process_platega_payment(call: CallbackQuery, plan_id: str, is_gift: bool) -> None:
    shop_id = db_settings.get("platega_shop_id")
    api_key = db_settings.get("platega_api_key")
    if not shop_id or not api_key:
        await call.answer("Platega.io не настроена", show_alert=True)
        return
    plan = await get_plan_or_reissue(plan_id)
    if not plan:
        await call.answer("Тариф не найден", show_alert=True)
        return
    user_id = call.from_user.id
    order_id = str(uuid.uuid4())
    hook_url = settings.platega_webhook_url
    success_url = f"https://t.me/{settings.bot_username}"
    result = await platega.create_platega_invoice(
        shop_id=shop_id,
        api_key=api_key,
        amount=plan.price,
        description=f"Adoria VPN — {plan.title}",
        order_id=order_id,
        hook_url=hook_url,
        success_url=success_url,
    )
    if not result:
        await call.answer("Ошибка создания счёта Platega", show_alert=True)
        return
    platega_id, pay_url = result
    await crud.create_invoice(
        user_id=user_id,
        plan_key=plan.id,
        plan_title=plan.title,
        days=plan.days,
        amount_rub=plan.price,
        currency="RUB",
        gateway="platega",
        invoice_id=order_id,
        label=order_id,
        is_gift=is_gift,
    )
    await call.message.edit_text(
        Texts.PLATEGA_INVOICE.format(title=plan.title, amount=f"{plan.price:.0f}"),
        reply_markup=invoice_kb(pay_url, order_id),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Check payment ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("check:"))
async def cb_check(call: CallbackQuery, state: FSMContext) -> None:
    invoice_id = call.data.split(":", 1)[1]
    inv = await crud.get_invoice(invoice_id)
    if not inv:
        await call.answer("Счёт не найден", show_alert=True)
        return
    if inv.status == "paid":
        if inv.is_gift and inv.gift_for_user_id is None:
            gc = await crud.get_or_create_invoice_gift_card(inv.invoice_id, inv.user_id, inv.days)
            if not gc:
                await call.answer("Не удалось получить подарочный ключ", show_alert=True)
                return
            bot_info = await call.bot.get_me()
            gift_link = f"https://t.me/{bot_info.username}?start=gift_{gc.code}"
            from bot.keyboards.inline import gift_sent_kb
            await call.message.edit_text(
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"🎁 <b>Ваш подарочный ключ на {inv.days} дней:</b>\n"
                f"<code>{gift_link}</code>\n\n"
                f"Отправьте эту ссылку тому, кому хотите подарить VPN, или отправьте на Email.",
                reply_markup=gift_sent_kb(gc.code),
                parse_mode="HTML",
            )
            await call.answer()
            return
        await call.answer("✅ Уже оплачено!", show_alert=True)
        return
    if inv.status in ("expired", "cancelled"):
        await call.answer("❌ Счёт истёк или отменён", show_alert=True)
        return

    if inv.gateway == "yoomoney":
        if inv.review_requested:
            await call.answer(
                "⏳ Заявка на проверку уже отправлена администратору. Ожидайте подтверждения.",
                show_alert=True,
            )
            return
        await state.set_state(UserState.wait_for_yoomoney_sender_name)
        await state.update_data(invoice_id=invoice_id)
        await call.message.answer(
            "🧾 Введите ФИО отправителя, с чьей карты или кошелька был перевод.\n"
            "Это нужно, чтобы администратор быстрее нашёл платёж."
        )
        await call.answer()
        return

    status = "active"
    if inv.gateway == "cryptopay":
        status = await cryptopay.get_crypto_invoice_status(invoice_id)
    elif inv.gateway == "platega":
        shop_id = db_settings.get("platega_shop_id")
        api_key = db_settings.get("platega_api_key")
        if shop_id and api_key:
            platega_status = await platega.get_platega_invoice_status(shop_id, api_key, invoice_id)
            if platega_status == "success":
                status = "paid"
    if status == "paid":
        await crud.update_invoice_status(invoice_id, "paid")
        recipient_id = inv.gift_for_user_id if inv.is_gift else inv.user_id
        
        if inv.plan_key == "reissue":
            from bot.handlers.user import _do_reissue
            user = await crud.get_user(recipient_id)
            if user:
                await _do_reissue(call, recipient_id, user.vpn_name)
            await call.message.edit_text(
                "✅ <b>Оплата подтверждена!</b>\nКлюч перевыпущен.",
                reply_markup=back_kb("my"),
                parse_mode="HTML",
            )
        else:
            if inv.is_gift:
                gc = await crud.get_or_create_invoice_gift_card(inv.invoice_id, inv.user_id, inv.days)
                if not gc:
                    await call.answer("Не удалось получить подарочный ключ", show_alert=True)
                    return
                bot_info = await call.bot.get_me()
                gift_link = f"https://t.me/{bot_info.username}?start=gift_{gc.code}"
                from bot.keyboards.inline import gift_sent_kb
                await call.message.edit_text(
                    f"✅ <b>Оплата подтверждена!</b>\n\n"
                    f"🎁 <b>Ваш подарочный ключ на {inv.days} дней:</b>\n"
                    f"<code>{gift_link}</code>\n\n"
                    f"Отправьте эту ссылку тому, кому хотите подарить VPN, или отправьте на Email.",
                    reply_markup=gift_sent_kb(gc.code),
                    parse_mode="HTML",
                )
            else:
                success = await deliver_vpn(call.bot, recipient_id, inv.days, is_gift=False)
                if success:
                    await call.message.edit_text(
                        "✅ <b>Оплата подтверждена!</b>\nVPN активирован. Проверьте раздел «Мой VPN».",
                        reply_markup=back_kb("my"),
                        parse_mode="HTML",
                    )

        # Log in payment channel
        pay_ch = db_settings.get("payment_channel_id")
        if pay_ch:
            try:
                await call.bot.send_message(
                    int(pay_ch),
                    f"✅ <b>Оплата</b>\n"
                    f"👤 {inv.user_id} | 📦 {inv.plan_title}\n"
                    f"💰 {inv.amount_rub}₽ | 💳 {inv.gateway}",
                    parse_mode="HTML",
                )
            except Exception:
                pass
    elif status == "expired":
        await crud.update_invoice_status(invoice_id, "expired")
        await call.answer("❌ Счёт истёк. Создайте новый.", show_alert=True)
    else:
        await call.answer("⏳ Оплата ещё не поступила. Попробуйте позже.", show_alert=True)


@router.message(UserState.wait_for_yoomoney_sender_name)
async def process_yoomoney_sender_name(message: Message, state: FSMContext) -> None:
    payer_name = " ".join((message.text or "").split())
    if len(payer_name) < 5:
        await message.answer("Укажите ФИО отправителя полностью, чтобы администратор смог найти перевод.")
        return

    data = await state.get_data()
    invoice_id = data.get("invoice_id")
    if not invoice_id:
        await state.clear()
        await message.answer("Не удалось найти счёт. Откройте оплату заново.")
        return

    inv = await crud.get_invoice(invoice_id)
    if not inv or inv.gateway != "yoomoney":
        await state.clear()
        await message.answer("Счёт не найден или уже недоступен.")
        return
    if inv.status != "active":
        await state.clear()
        await message.answer("Этот счёт уже не требует проверки.")
        return
    if inv.review_requested:
        await state.clear()
        await message.answer("Заявка по этому счёту уже отправлена администратору.")
        return

    updated_inv = await crud.request_invoice_manual_review(invoice_id, payer_name)
    await state.clear()
    if not updated_inv:
        await message.answer("Не удалось обновить счёт. Попробуйте ещё раз.")
        return

    await _notify_yoomoney_manual_review(message.bot, updated_inv)
    await message.answer(
        "✅ Заявка на проверку отправлена администратору.\n"
        "Как только платёж подтвердят, VPN будет выдан автоматически."
    )


# ─── Cancel ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(call: CallbackQuery) -> None:
    invoice_id = call.data.split(":", 1)[1]
    inv = await crud.get_invoice(invoice_id)
    if not inv:
        await call.answer("Счёт не найден", show_alert=True)
        return
    if inv.status == "paid":
        await call.answer("Оплаченный счёт нельзя отменить.", show_alert=True)
        return
    if inv.status != "active":
        await call.answer("Этот счёт уже не активен.", show_alert=True)
        return
    await crud.update_invoice_status(invoice_id, "cancelled")
    await call.message.edit_text("❌ Счёт отменён.", reply_markup=back_kb("buy"))
    await call.answer("Счёт отменён")
