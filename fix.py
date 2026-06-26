import re

with open(r"bot\handlers\payments.py", "r", encoding="utf-8") as f:
    content = f.read()

# We need to find `async def _process_tome_payment(call: CallbackQuery, plan_id: str, is_gift: bool) -> None:`
# And replace everything up to the next `# ─── YooMoney payment ───`
# Since it's mangled, we can just cut it out and insert the new one.

start_idx = content.find("async def _process_tome_payment")
end_idx = content.find("# ─── YooMoney payment")

if start_idx != -1 and end_idx != -1:
    new_func = """async def _process_tome_payment(call: CallbackQuery, plan_id: str, is_gift: bool) -> None:
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
        f"💳 <b>Новый платёж (СБП)</b>\\n\\n"
        f"👤 {call.from_user.id} (@{call.from_user.username or '—'})\\n"
        f"📦 {'Подарок: ' if is_gift else ''}{plan.title}\\n"
        f"💰 {plan.price}₽\\n"
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
        f"📱 <b>Оплата через СБП</b>\\n\\n"
        f"📦 Тариф: <b>{plan.title}</b>\\n"
        f"💰 Сумма: <b>{plan.price}₽</b>\\n\\n"
        f"Переведите <b>{plan.price}₽</b> по СБП:\\n"
        f"📞 <code>{tome_phone}</code>\\n"
        f"🏦 {tome_bank}\\n\\n"
        f"В комментарии укажите: <code>{invoice_id}</code>\\n\\n"
        f"✅ После перевода администратор подтвердит платёж вручную.",
        reply_markup=back_kb("buy"),
        parse_mode="HTML",
    )
    await call.answer()


"""
    content = content[:start_idx] + new_func + content[end_idx:]
    with open(r"bot\handlers\payments.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Fixed.")
else:
    print("Could not find boundaries")
