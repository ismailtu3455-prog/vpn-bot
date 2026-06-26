import re

with open(r"bot\handlers\user.py", "r", encoding="utf-8") as f:
    content = f.read()

old_gift_block = """    # Handle gift card
    if args.startswith("gift_"):
        code = args[5:]
        gc = await crud.use_gift_card(code, user_id)
        if gc:
            bot_obj = bot
            await deliver_vpn(bot_obj, user_id, gc.days, is_gift=True)
            await message.answer(
                f"🎁 <b>Подарочная карта активирована!</b>\\nВам выданы <b>{gc.days} дней</b> VPN.",
                parse_mode="HTML",
            )
            return
        else:
            await message.answer("❌ Подарочная карта не найдена или уже использована.")"""

new_gift_block = """    # Handle gift card
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
                "❓ <b>Вы уверены, что хотите активировать свою же подарочную карту?</b>\\n"
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
                f"🎁 <b>Подарочная карта активирована!</b>\\nВам выданы <b>{gc.days} дней</b> VPN.",
                parse_mode="HTML",
            )
            return
        else:
            await message.answer("❌ Подарочная карта не найдена или уже использована.")
            return"""

content = content.replace(old_gift_block, new_gift_block)

# Now we need to append the callback query handlers to the end of the file.
callback_handlers = """
@router.callback_query(F.data.startswith("confirm_gift:"))
async def cb_confirm_gift(call: CallbackQuery) -> None:
    code = call.data.split(":")[1]
    user_id = call.from_user.id
    
    gc = await crud.use_gift_card(code, user_id)
    if gc:
        await deliver_vpn(call.bot, user_id, gc.days, is_gift=True)
        await call.message.edit_text(
            f"🎁 <b>Подарочная карта активирована!</b>\\nВам выданы <b>{gc.days} дней</b> VPN.",
            parse_mode="HTML",
        )
    else:
        await call.message.edit_text("❌ Подарочная карта не найдена или уже использована.")
    await call.answer()

@router.callback_query(F.data == "cancel_gift")
async def cb_cancel_gift(call: CallbackQuery) -> None:
    await call.message.edit_text("❌ Активация подарочной карты отменена.")
    await call.answer()
"""

content += "\n" + callback_handlers

with open(r"bot\handlers\user.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Patch successful!")
