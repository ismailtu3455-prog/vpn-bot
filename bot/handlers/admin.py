from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import Router, F
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.config import settings, db_settings
from bot.database import crud
from bot.keyboards.inline import (
    admin_menu_kb, admin_manage_kb, admin_channels_setup_kb,
    admin_list_kb, plans_list_kb, promos_list_kb, admin_test_setup_kb,
    admin_payments_kb, admin_ref_setup_kb, promo_type_kb, invoices_list_kb,
    user_manage_kb, withdrawal_decision_kb, admin_server_status_kb,
    admin_vpn_actions_kb, back_kb,
)
from bot.services import vpn as vpn_service
from bot.services.delivery import deliver_vpn, deliver_gift
from bot.states import (
    AdminState, AdminPaymentState, AdminSettingsState, AdminInvoiceState, SupportState,
)
from bot.texts import Texts

logger = logging.getLogger(__name__)
router = Router()

INVOICES_PER_PAGE = 10


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = event.from_user.id  # type: ignore[union-attr]
        if user_id in settings.get_admin_ids:
            return True
        admins = await crud.get_admins()
        return any(a.user_id == user_id for a in admins)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ─── Home ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:home")
async def adm_home(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(Texts.ADMIN_HOME, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await call.answer()


# ─── Stats ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:stats")
async def adm_stats(call: CallbackQuery) -> None:
    stats = await crud.get_dashboard_stats_extended()
    text = (
        f"📊 <b>Статистика Adoria VPN</b>\n\n"
        f"👥 Пользователей: <b>{stats['total_users']}</b>\n"
        f"🆕 Сегодня: <b>{stats['new_users_today']}</b>\n"
        f"🌐 С VPN: <b>{stats['users_with_vpn']}</b>\n\n"
        f"✅ Оплачено: <b>{stats['paid_invoices']}</b>\n"
        f"⏳ Активных: <b>{stats['active_invoices']}</b>\n\n"
        f"💰 Выручка: <b>{stats['revenue']:.2f}₽</b>\n"
        f"📈 Сегодня: <b>{stats['revenue_today']:.2f}₽</b>"
    )
    await call.message.edit_text(text, reply_markup=back_kb("adm:home"), parse_mode="HTML")
    await call.answer()


# ─── Manage ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:manage")
async def adm_manage(call: CallbackQuery) -> None:
    await call.message.edit_text("👥 <b>Управление пользователями</b>", reply_markup=admin_manage_kb(), parse_mode="HTML")
    await call.answer()


# ─── Find user ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:find_user")
async def adm_find_user(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminState.find_user)
    await call.message.edit_text("🔍 Введите ID или @username пользователя:")
    await call.answer()


@router.message(AdminState.find_user)
async def process_find_user(message: Message, state: FSMContext) -> None:
    await state.clear()
    query = message.text.strip()
    user = await crud.get_user_by_id_or_username(query)
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    refs = await crud.get_referrals_count(user.user_id)
    paid = await crud.get_paid_invoices_count(user.user_id)
    vpn_info = f"<code>{user.vpn_name}</code>" if user.vpn_name else "нет"
    ban_mark = " 🚫" if user.vpn_banned else ""
    text = (
        f"👤 <b>Пользователь</b>\n\n"
        f"🆔 ID: <code>{user.user_id}</code>\n"
        f"👤 @{user.username or 'нет'}{ban_mark}\n"
        f"📛 {user.first_name or ''} {user.last_name or ''}\n"
        f"💰 Баланс: <b>{user.balance:.2f}₽</b>\n"
        f"🌐 VPN: {vpn_info}\n"
        f"👥 Рефералов: <b>{refs}</b>\n"
        f"💳 Оплачено: <b>{paid}</b>\n"
        f"📅 Регистрация: {user.created_at.strftime('%d.%m.%Y') if user.created_at else 'н/д'}"
    )
    await message.answer(text, reply_markup=user_manage_kb(user.user_id, user.vpn_banned), parse_mode="HTML")


# ─── Balance ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:add_balance:"))
async def adm_add_balance_start(call: CallbackQuery, state: FSMContext) -> None:
    uid = int(call.data.split(":")[2])
    await state.update_data(target_user_id=uid)
    await state.set_state(AdminState.wait_for_balance_amount)
    await call.message.edit_text(f"💰 Введите сумму для пополнения баланса пользователя {uid}:")
    await call.answer()


@router.message(AdminState.wait_for_balance_amount)
async def process_balance_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    uid = data.get("target_user_id")
    try:
        amount = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("❌ Неверная сумма")
        return
    await crud.add_user_balance(uid, amount)
    await message.answer(f"✅ Баланс пользователя {uid} пополнен на {amount}₽")
    try:
        await message.bot.send_message(uid, f"💰 Ваш баланс пополнен на <b>{amount}₽</b>", parse_mode="HTML")
    except Exception:
        pass


# ─── Grant VPN ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:grant_vpn:"))
async def adm_grant_vpn_start(call: CallbackQuery, state: FSMContext) -> None:
    uid = int(call.data.split(":")[2])
    await state.update_data(target_user_id=uid)
    await state.set_state(AdminState.grant_days)
    await call.message.edit_text(f"📅 Введите кол-во дней для выдачи VPN пользователю {uid}:")
    await call.answer()


@router.message(AdminState.grant_days)
async def process_grant_days(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    uid = data.get("target_user_id")
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверное количество дней")
        return
    success = await deliver_vpn(message.bot, uid, days)
    if success:
        await message.answer(f"✅ VPN выдан пользователю {uid} на {days} дней")
    else:
        await message.answer(f"❌ Ошибка выдачи VPN пользователю {uid}")


# ─── Delete user ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:del_user:"))
async def adm_del_user(call: CallbackQuery) -> None:
    uid = int(call.data.split(":")[2])
    user = await crud.get_user(uid)
    if user and user.vpn_name:
        try:
            await vpn_service.delete_client(user.vpn_name)
        except Exception:
            pass
    await crud.delete_user(uid)
    await call.message.edit_text(f"🗑 Пользователь {uid} удалён.", reply_markup=back_kb("adm:manage"))
    await call.answer()


# ─── Ban VPN ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:ban_vpn:"))
async def adm_ban_vpn(call: CallbackQuery) -> None:
    uid = int(call.data.split(":")[2])
    user = await crud.get_user(uid)
    if not user:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    if not user.vpn_name:
        await call.answer("У пользователя нет VPN", show_alert=True)
        return
    try:
        await vpn_service.ban_client(user.vpn_name, reason="Admin ban")
        await crud.set_user_vpn_banned(uid, True)
        await call.bot.send_message(
            uid,
            Texts.BANNED_VPN.format(reason="Нарушение правил использования"),
            parse_mode="HTML",
        )
        await call.message.edit_text(
            f"🚫 VPN пользователя {uid} заблокирован.",
            reply_markup=user_manage_kb(uid, is_vpn_banned=True),
        )
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)
    await call.answer()


@router.callback_query(F.data.startswith("adm:unban_vpn:"))
async def adm_unban_vpn(call: CallbackQuery) -> None:
    uid = int(call.data.split(":")[2])
    user = await crud.get_user(uid)
    if not user:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    if not user.vpn_name:
        await call.answer("У пользователя нет VPN", show_alert=True)
        return
    try:
        await vpn_service.unban_client(user.vpn_name)
        await crud.set_user_vpn_banned(uid, False)
        await call.bot.send_message(uid, Texts.UNBANNED_VPN, parse_mode="HTML")
        await call.message.edit_text(
            f"✅ VPN пользователя {uid} разблокирован.",
            reply_markup=user_manage_kb(uid, is_vpn_banned=False),
        )
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)
    await call.answer()


@router.callback_query(F.data.startswith("adm:del_vpn:"))
async def adm_del_vpn(call: CallbackQuery) -> None:
    uid = int(call.data.split(":")[2])
    user = await crud.get_user(uid)
    if not user or not user.vpn_name:
        await call.answer("VPN не найден", show_alert=True)
        return
    try:
        await vpn_service.delete_client(user.vpn_name)
    except Exception:
        pass
    await crud.set_vpn_name(uid, None)
    await call.message.edit_text(f"🗑 VPN ключ пользователя {uid} удалён.", reply_markup=back_kb("adm:manage"))
    await call.answer()


# ─── Server status ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:server_status")
async def adm_server_status(call: CallbackQuery) -> None:
    try:
        statuses = await vpn_service.get_all_server_statuses()
        lines = ["🖥 <b>Статус серверов VPN</b>\n"]
        for s in statuses:
            name = s.get("name", "?")
            if not s.get("ok"):
                lines.append(f"❌ <b>{name}</b>: {s.get('error', 'недоступен')}\n")
                continue
            cpu = s.get("cpu") or s.get("cpuList") or "?"
            if isinstance(cpu, list):
                cpu = f"{round(cpu[0], 1)}%" if cpu else "?"
            mem = s.get("mem") or {}
            ram_used = mem.get("current", 0)
            ram_total = mem.get("total", 0)
            ram_str = f"{round(ram_used/1024/1024/1024,1)}/{round(ram_total/1024/1024/1024,1)} ГБ" if ram_total else "?"
            uptime = s.get("uptime", "?")
            xray_state = s.get("xray", {}).get("state", "?") if isinstance(s.get("xray"), dict) else "?"
            lines.append(
                f"🟢 <b>{name}</b>\n"
                f"   ⚙️ Xray: {xray_state} | CPU: {cpu}\n"
                f"   🧠 RAM: {ram_str} | ⬆️ {uptime}\n"
            )
        text = "\n".join(lines)
    except Exception as e:
        text = f"🖥 <b>VPN Серверы</b>\n\n❌ Ошибка: {e}"
    await call.message.edit_text(text, reply_markup=admin_server_status_kb(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "adm:vpn_stats")
async def adm_vpn_stats(call: CallbackQuery) -> None:
    try:
        statuses = await vpn_service.get_all_server_statuses()
        lines = ["📊 <b>Трафик VPN по серверам</b>\n"]
        for s in statuses:
            name = s.get("name", "?")
            if not s.get("ok"):
                lines.append(f"❌ <b>{name}</b>: недоступен\n")
                continue
            net = s.get("netIO") or s.get("netTraffic") or {}
            sent = net.get("up", 0)
            recv = net.get("down", 0)
            sent_gb = round(sent / 1_073_741_824, 2)
            recv_gb = round(recv / 1_073_741_824, 2)
            lines.append(
                f"🌐 <b>{name}</b>\n"
                f"   ⬆️ Отправлено: {sent_gb} ГБ\n"
                f"   ⬇️ Получено: {recv_gb} ГБ\n"
            )
        text = "\n".join(lines)
    except Exception as e:
        text = f"❌ Ошибка: {e}"
    await call.message.edit_text(text, reply_markup=admin_server_status_kb(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "adm:vpn_logs")
async def adm_vpn_logs(call: CallbackQuery) -> None:
    try:
        logs = await vpn_service.get_logs(30)
        d = logs.get("data") or logs
        lines = d if isinstance(d, list) else [str(d)]
        log_text = "\n".join(str(l) for l in lines[:20])
        text = f"📋 <b>Логи VPN</b>\n\n<code>{log_text[:3000]}</code>"
    except Exception as e:
        text = f"❌ Ошибка: {e}"
    await call.message.edit_text(text, reply_markup=admin_server_status_kb(), parse_mode="HTML")
    await call.answer()


# ─── Payments setup ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:payments")
async def adm_payments(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "💳 <b>Настройка платёжных методов</b>",
        reply_markup=admin_payments_kb(db_settings),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "adm:pay_setup:crypto")
async def adm_pay_crypto(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminPaymentState.wait_for_crypto_token)
    current = db_settings.get("crypto_pay_token") or "не задан"
    await call.message.edit_text(
        f"₿ <b>CryptoBot токен</b>\n\nТекущий: <code>{current}</code>\n\nВведите новый токен (или 'disable' для отключения):"
    )
    await call.answer()


@router.message(AdminPaymentState.wait_for_crypto_token)
async def process_crypto_token(message: Message, state: FSMContext) -> None:
    await state.clear()
    val = None if message.text.strip().lower() == "disable" else message.text.strip()
    await crud.set_setting("crypto_pay_token", val)
    from bot.services import cryptopay
    cryptopay._reset_client()
    await message.answer(f"✅ CryptoBot {'отключён' if not val else 'настроен'}")


@router.callback_query(F.data == "adm:pay_setup:stars")
async def adm_pay_stars(call: CallbackQuery) -> None:
    current = db_settings.get("stars_enabled", "1") == "1"
    new_val = "0" if current else "1"
    await crud.set_setting("stars_enabled", new_val)
    await call.message.edit_text(
        f"⭐ Telegram Stars: {'✅ включены' if new_val == '1' else '❌ отключены'}",
        reply_markup=back_kb("adm:payments"),
    )
    await call.answer()


@router.callback_query(F.data == "adm:pay_setup:yoomoney")
async def adm_pay_yoomoney(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminPaymentState.wait_for_yoomoney_wallet)
    ym_w = db_settings.get("yoomoney_wallet") or "не задан"
    ym_s = db_settings.get("yoomoney_secret") or "не задан"
    await call.message.edit_text(
        f"💛 <b>YooMoney настройки</b>\n\n"
        f"Кошелёк: <code>{ym_w}</code>\n"
        f"Секрет: <code>{ym_s}</code>\n\n"
        f"Webhook URL: <code>{settings.yoomoney_webhook_url}</code>\n\n"
        f"Введите через пробел: <code>wallet_id notification_secret</code>\n"
        f"Или 'disable' для отключения:"
    )
    await call.answer()


@router.message(AdminPaymentState.wait_for_yoomoney_wallet)
async def process_yoomoney_creds(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = message.text.strip()
    if text.lower() == "disable":
        await crud.set_setting("yoomoney_wallet", None)
        await crud.set_setting("yoomoney_secret", None)
        await message.answer("💛 YooMoney отключён")
        return
    parts = text.split()
    if len(parts) != 2:
        await message.answer("❌ Введите: wallet_id secret (через пробел)")
        return
    wallet, secret = parts
    await crud.set_setting("yoomoney_wallet", wallet)
    await crud.set_setting("yoomoney_secret", secret)
    await message.answer(f"✅ YooMoney настроен. Кошелёк: {wallet}")


@router.callback_query(F.data == "adm:pay_setup:lava")
async def adm_pay_lava(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminPaymentState.wait_for_lava_creds)
    shop = db_settings.get("lava_shop_id") or "не задан"
    await call.message.edit_text(
        f"🟢 <b>Lava.ru СБП настройки</b>\n\n"
        f"Shop ID: <code>{shop}</code>\n\n"
        f"Введите через пробел: <code>shop_id api_key</code>\n"
        f"Или 'disable' для отключения:"
    )
    await call.answer()


@router.message(AdminPaymentState.wait_for_lava_creds)
async def process_lava_creds(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = message.text.strip()
    if text.lower() == "disable":
        await crud.set_setting("lava_shop_id", None)
        await crud.set_setting("lava_api_key", None)
        await message.answer("🟢 Lava.ru отключён")
        return
    parts = text.split()
    if len(parts) != 2:
        await message.answer("❌ Введите: shop_id api_key (через пробел)")
        return
    shop_id, api_key = parts
    await crud.set_setting("lava_shop_id", shop_id)
    await crud.set_setting("lava_api_key", api_key)
    await message.answer(f"✅ Lava.ru настроен. Shop ID: {shop_id}")


@router.callback_query(F.data == "adm:pay_setup:tome")
async def adm_pay_tome(call: CallbackQuery, state: FSMContext) -> None:
    current = db_settings.get("tome_enabled", "0") == "1"
    if current:
        await crud.set_setting("tome_enabled", "0")
        await call.answer("📱 СБП (tome) отключён")
        await adm_payments(call)
        return
    await state.set_state(AdminPaymentState.wait_for_tome_phone)
    await call.message.edit_text("📱 Введите номер телефона для СБП (tome):")
    await call.answer()


@router.message(AdminPaymentState.wait_for_tome_phone)
async def process_tome_phone(message: Message, state: FSMContext) -> None:
    await state.update_data(tome_phone=message.text.strip())
    await state.set_state(AdminPaymentState.wait_for_tome_bank)
    await message.answer("🏦 Введите название банка:")


@router.message(AdminPaymentState.wait_for_tome_bank)
async def process_tome_bank(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await crud.set_setting("tome_phone", data.get("tome_phone"))
    await crud.set_setting("tome_bank", message.text.strip())
    await crud.set_setting("tome_enabled", "1")
    await message.answer("✅ СБП (tome) настроен")


@router.callback_query(F.data == "adm:toggle_balance_pay")
async def adm_toggle_balance_pay(call: CallbackQuery) -> None:
    current = db_settings.get("balance_pay_enabled", "1") == "1"
    new_val = "0" if current else "1"
    await crud.set_setting("balance_pay_enabled", new_val)
    await call.message.edit_text(
        f"💰 Оплата балансом: {'✅ включена' if new_val == '1' else '❌ отключена'}",
        reply_markup=back_kb("adm:payments"),
    )
    await call.answer()


# ─── Admins ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:admins")
async def adm_admins(call: CallbackQuery) -> None:
    admins = await crud.get_admins()
    await call.message.edit_text("👮 <b>Администраторы</b>", reply_markup=admin_list_kb(admins), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("adm:del_admin:"))
async def adm_del_admin(call: CallbackQuery) -> None:
    uid = int(call.data.split(":")[2])
    if uid in settings.get_admin_ids:
        await call.answer("Нельзя удалить основного администратора", show_alert=True)
        return
    await crud.remove_admin(uid)
    await adm_admins(call)


@router.callback_query(F.data == "adm:add_admin")
async def adm_add_admin_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_admin_id)
    await call.message.edit_text("👤 Введите ID пользователя для добавления в администраторы:")
    await call.answer()


@router.message(AdminSettingsState.wait_for_admin_id)
async def process_add_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not message.text.strip().isdigit():
        await message.answer("❌ Введите числовой ID")
        return
    uid = int(message.text.strip())
    await crud.add_admin(uid)
    await message.answer(f"✅ Пользователь {uid} добавлен в администраторы")


# ─── Plans ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:plans")
async def adm_plans(call: CallbackQuery) -> None:
    plans = await crud.get_all_plans()
    await call.message.edit_text("📦 <b>Тарифы</b>", reply_markup=plans_list_kb(plans), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("adm:del_plan:"))
async def adm_del_plan(call: CallbackQuery) -> None:
    plan_id = call.data.split(":")[2]
    await crud.delete_plan(plan_id)
    await adm_plans(call)


@router.callback_query(F.data == "adm:add_plan")
async def adm_add_plan(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_plan_id)
    await call.message.edit_text("📦 Введите ID тарифа (например: 7d, 1m, vip):")
    await call.answer()


@router.message(AdminSettingsState.wait_for_plan_id)
async def plan_wait_id(message: Message, state: FSMContext) -> None:
    await state.update_data(plan_id=message.text.strip())
    await state.set_state(AdminSettingsState.wait_for_plan_title)
    await message.answer("📛 Введите название тарифа:")


@router.message(AdminSettingsState.wait_for_plan_title)
async def plan_wait_title(message: Message, state: FSMContext) -> None:
    await state.update_data(plan_title=message.text.strip())
    await state.set_state(AdminSettingsState.wait_for_plan_days)
    await message.answer("📅 Введите количество дней:")


@router.message(AdminSettingsState.wait_for_plan_days)
async def plan_wait_days(message: Message, state: FSMContext) -> None:
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число")
        return
    await state.update_data(plan_days=days)
    await state.set_state(AdminSettingsState.wait_for_plan_price)
    await message.answer("💰 Введите цену в рублях:")


@router.message(AdminSettingsState.wait_for_plan_price)
async def plan_wait_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        price = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите число")
        return
    plan = await crud.create_plan(data["plan_id"], data["plan_title"], data["plan_days"], price)
    await message.answer(f"✅ Тариф «{plan.title}» создан: {plan.days}д за {plan.price}₽")


# ─── Promo codes ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:promos")
async def adm_promos(call: CallbackQuery) -> None:
    promos = await crud.get_all_promocodes()
    await call.message.edit_text("🎟 <b>Промокоды</b>", reply_markup=promos_list_kb(promos), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("adm:del_promo:"))
async def adm_del_promo(call: CallbackQuery) -> None:
    code = call.data.split(":", 2)[2]
    await crud.delete_promocode(code)
    await adm_promos(call)


@router.callback_query(F.data == "adm:add_promo")
async def adm_add_promo(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_promo_code)
    await call.message.edit_text("🎟 Введите код промокода:")
    await call.answer()


@router.message(AdminSettingsState.wait_for_promo_code)
async def promo_wait_code(message: Message, state: FSMContext) -> None:
    await state.update_data(promo_code=message.text.strip().upper())
    await state.set_state(AdminSettingsState.wait_for_promo_type)
    await message.answer("📋 Выберите тип промокода:", reply_markup=promo_type_kb())


@router.callback_query(F.data.startswith("promo_type:"))
async def promo_choose_type(call: CallbackQuery, state: FSMContext) -> None:
    promo_type = call.data.split(":")[1]
    await state.update_data(promo_type=promo_type)
    await state.set_state(AdminSettingsState.wait_for_promo_value)
    labels = {"days": "дней", "discount": "% скидку", "balance": "₽ на баланс"}
    await call.message.edit_text(f"💎 Введите значение ({labels.get(promo_type, 'значение')}):")
    await call.answer()


@router.message(AdminSettingsState.wait_for_promo_value)
async def promo_wait_value(message: Message, state: FSMContext) -> None:
    try:
        value = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите число")
        return
    await state.update_data(promo_value=value)
    await state.set_state(AdminSettingsState.wait_for_promo_uses)
    await message.answer("🔢 Введите максимальное количество использований:")


@router.message(AdminSettingsState.wait_for_promo_uses)
async def promo_wait_uses(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        uses = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число")
        return
    promo = await crud.create_promocode(data["promo_code"], data["promo_type"], data["promo_value"], uses)
    await message.answer(f"✅ Промокод <code>{promo.code}</code> создан: {promo.promo_type}={promo.value}, использований: {uses}", parse_mode="HTML")


# ─── Referral settings ────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:ref_setup")
async def adm_ref_setup(call: CallbackQuery) -> None:
    await call.message.edit_text("👥 <b>Реферальная программа</b>", reply_markup=admin_ref_setup_kb(db_settings), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "adm:set_ref_start")
async def adm_set_ref_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_ref_start)
    await call.message.edit_text("🎁 Введите сумму бонуса за старт (₽):")
    await call.answer()


@router.message(AdminSettingsState.wait_for_ref_start)
async def process_ref_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await crud.set_setting("ref_reward_start", message.text.strip())
    await message.answer(f"✅ Бонус за старт: {message.text.strip()}₽")


@router.callback_query(F.data == "adm:set_ref_lvl1")
async def adm_set_ref_lvl1(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_ref_lvl1)
    await call.message.edit_text("📊 Введите процент уровня 1 (%):")
    await call.answer()


@router.message(AdminSettingsState.wait_for_ref_lvl1)
async def process_ref_lvl1(message: Message, state: FSMContext) -> None:
    await state.clear()
    await crud.set_setting("ref_percent_lvl1", message.text.strip())
    await message.answer(f"✅ Уровень 1: {message.text.strip()}%")


@router.callback_query(F.data == "adm:set_ref_lvl2")
async def adm_set_ref_lvl2(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_ref_lvl2)
    await call.message.edit_text("📊 Введите процент уровня 2 (%):")
    await call.answer()


@router.message(AdminSettingsState.wait_for_ref_lvl2)
async def process_ref_lvl2(message: Message, state: FSMContext) -> None:
    await state.clear()
    await crud.set_setting("ref_percent_lvl2", message.text.strip())
    await message.answer(f"✅ Уровень 2: {message.text.strip()}%")


# ─── Test settings ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:test_setup")
async def adm_test_setup(call: CallbackQuery) -> None:
    await call.message.edit_text("🧪 <b>Тест период</b>", reply_markup=admin_test_setup_kb(db_settings), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "adm:toggle_test")
async def adm_toggle_test(call: CallbackQuery) -> None:
    current = db_settings.get("test_enabled", "1") == "1"
    await crud.set_setting("test_enabled", "0" if current else "1")
    await adm_test_setup(call)


@router.callback_query(F.data == "adm:set_test_days")
async def adm_set_test_days(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_test_days)
    await call.message.edit_text("📅 Введите количество дней тест-периода:")
    await call.answer()


@router.message(AdminSettingsState.wait_for_test_days)
async def process_test_days(message: Message, state: FSMContext) -> None:
    await state.clear()
    await crud.set_setting("test_days", message.text.strip())
    await message.answer(f"✅ Тест-период: {message.text.strip()} дней")


@router.callback_query(F.data == "adm:set_limit")
async def adm_set_limit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_limit_gb)
    await call.message.edit_text("📊 Введите лимит трафика (ГБ, 0 = безлимит):")
    await call.answer()


@router.message(AdminSettingsState.wait_for_limit_gb)
async def process_limit_gb(message: Message, state: FSMContext) -> None:
    await state.clear()
    await crud.set_setting("default_limit_gb", message.text.strip())
    await message.answer(f"✅ Лимит трафика: {message.text.strip()} ГБ")


# ─── Channel settings ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:channels_setup")
async def adm_channels_setup(call: CallbackQuery) -> None:
    await call.message.edit_text("🔗 <b>Настройка каналов</b>", reply_markup=admin_channels_setup_kb(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "adm:set_main_channel")
async def adm_set_main_channel(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_main_channel)
    await call.message.edit_text("📢 Перешлите любое сообщение из основного канала или введите ID канала:")
    await call.answer()


@router.message(AdminSettingsState.wait_for_main_channel)
async def process_main_channel(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.forward_from_chat:
        channel_id = message.forward_from_chat.id
        await crud.set_setting("main_channel_id", str(channel_id))
        if message.forward_from_chat.invite_link:
            await crud.set_setting("main_channel_url", message.forward_from_chat.invite_link)
        await message.answer(f"✅ Основной канал: {channel_id}")
    else:
        text = message.text.strip()
        await crud.set_setting("main_channel_id", text)
        await message.answer(f"✅ Основной канал ID: {text}")


@router.callback_query(F.data == "adm:set_pay_channel")
async def adm_set_pay_channel(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSettingsState.wait_for_payment_channel)
    await call.message.edit_text("💳 Перешлите сообщение из канала платежей или введите ID:")
    await call.answer()


@router.message(AdminSettingsState.wait_for_payment_channel)
async def process_pay_channel(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.forward_from_chat:
        channel_id = message.forward_from_chat.id
        await crud.set_setting("payment_channel_id", str(channel_id))
        await message.answer(f"✅ Канал платежей: {channel_id}")
    else:
        text = message.text.strip()
        await crud.set_setting("payment_channel_id", text)
        await message.answer(f"✅ Канал платежей ID: {text}")


# ─── Invoices ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:payinvoice:"))
async def adm_payinvoice(call: CallbackQuery) -> None:
    page = int(call.data.split(":")[2])
    invoices, total = await crud.get_all_invoices_paginated(page=page, per_page=INVOICES_PER_PAGE)
    kb = invoices_list_kb(invoices, page, "date", False, total, INVOICES_PER_PAGE, mode="all")
    await call.message.edit_text(
        f"📋 <b>Счета</b> (страница {page + 1}, всего: {total})",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:payreview:"))
async def adm_payreview(call: CallbackQuery) -> None:
    page = int(call.data.split(":")[2])
    invoices, total = await crud.get_review_invoices_paginated(page=page, per_page=INVOICES_PER_PAGE)
    kb = invoices_list_kb(invoices, page, "date", False, total, INVOICES_PER_PAGE, mode="review")
    await call.message.edit_text(
        f"🕐 <b>Счета на ручную проверку</b> (страница {page + 1}, всего: {total})",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:inv_detail:"))
async def adm_inv_detail(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    inv_db_id = int(parts[2])
    mode = parts[3] if len(parts) > 3 else "all"
    inv = await crud.get_invoice_by_db_id(inv_db_id)
    if not inv:
        await call.answer("Счёт не найден", show_alert=True)
        return
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    if inv.status == "active":
        builder.row(InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm:inv_approve:{inv_db_id}"))
    back_callback = "adm:payreview:0" if mode == "review" else "adm:payinvoice:0"
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback))
    text = (
        f"📋 <b>Счёт #{inv.id}</b>\n\n"
        f"👤 Пользователь: {inv.user_id}\n"
        f"📦 Тариф: {html.escape(inv.plan_title)}\n"
        f"💳 Шлюз: {html.escape(inv.gateway)}\n"
        f"💰 Сумма: {inv.amount_rub}₽\n"
        f"📊 Статус: {inv.status}\n"
        f"🧾 ФИО отправителя: {html.escape(inv.payer_name or 'не указано')}\n"
        f"🕐 Запрошена проверка: {'да' if inv.review_requested else 'нет'}\n"
        f"🔑 Invoice ID: {inv.invoice_id or 'н/д'}\n"
        f"📅 Создан: {inv.created_at.strftime('%d.%m.%Y %H:%M') if inv.created_at else 'н/д'}"
    )
    if inv.review_requested_at:
        text += f"\n📨 Заявка: {inv.review_requested_at.strftime('%d.%m.%Y %H:%M')}"
    await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("adm:inv_approve:"))
async def adm_inv_approve(call: CallbackQuery) -> None:
    inv_db_id = int(call.data.split(":")[2])
    inv = await crud.get_invoice_by_db_id(inv_db_id)
    if not inv:
        await call.answer("Счёт не найден", show_alert=True)
        return
    if inv.status != "active":
        await call.answer("Счёт уже обработан", show_alert=True)
        return
    if inv.invoice_id:
        await crud.update_invoice_status(inv.invoice_id, "paid")
    recipient_id = inv.gift_for_user_id if inv.is_gift else inv.user_id
    if inv.is_gift and not inv.gift_for_user_id:
        gift_card = await crud.get_or_create_invoice_gift_card(inv.invoice_id, inv.user_id, inv.days)
        if gift_card:
            gift_link = f"https://t.me/{settings.bot_username}?start=gift_{gift_card.code}"
            from bot.keyboards.inline import gift_sent_kb
            await call.bot.send_message(
                inv.user_id,
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"🎁 <b>Ваш подарочный ключ на {inv.days} дней:</b>\n"
                f"<code>{gift_link}</code>\n\n"
                f"Отправьте эту ссылку тому, кому хотите подарить VPN, или отправьте на Email.",
                reply_markup=gift_sent_kb(gift_card.code),
                parse_mode="HTML",
            )
            success = True
        else:
            success = False
    elif inv.is_gift and inv.gift_for_user_id:
        recipient = await crud.get_user(inv.gift_for_user_id)
        if recipient and recipient.email:
            success = await deliver_gift(inv.user_id, inv.days, recipient_email=recipient.email)
        else:
            success = await deliver_vpn(call.bot, inv.gift_for_user_id, inv.days, is_gift=True)
    else:
        success = await deliver_vpn(call.bot, recipient_id, inv.days)
    if success:
        await call.answer("✅ VPN выдан!")
        # notify in payment channel
        pay_ch = db_settings.get("payment_channel_id")
        if pay_ch:
            try:
                await call.bot.send_message(
                    int(pay_ch),
                    f"✅ Счёт #{inv.id} одобрен. "
                    f"{'Подарочная ссылка отправлена покупателю.' if inv.is_gift else f'VPN выдан пользователю {recipient_id}.'}",
                )
            except Exception:
                pass
    else:
        await call.answer("❌ Ошибка выдачи VPN", show_alert=True)


@router.callback_query(F.data.startswith("adm:inv_approve_by_ext:"))
async def adm_inv_approve_by_ext(call: CallbackQuery) -> None:
    invoice_id = call.data.split(":", 2)[2]
    inv = await crud.get_invoice(invoice_id)
    if not inv:
        await call.answer("Счёт не найден", show_alert=True)
        return
    if inv.status != "active":
        await call.answer("Счёт уже обработан", show_alert=True)
        return
    await crud.update_invoice_status(invoice_id, "paid")
    recipient_id = inv.gift_for_user_id if inv.is_gift else inv.user_id
    if inv.is_gift and not inv.gift_for_user_id:
        gift_card = await crud.get_or_create_invoice_gift_card(inv.invoice_id, inv.user_id, inv.days)
        if gift_card:
            gift_link = f"https://t.me/{settings.bot_username}?start=gift_{gift_card.code}"
            from bot.keyboards.inline import gift_sent_kb
            await call.bot.send_message(
                inv.user_id,
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"🎁 <b>Ваш подарочный ключ на {inv.days} дней:</b>\n"
                f"<code>{gift_link}</code>\n\n"
                f"Отправьте эту ссылку тому, кому хотите подарить VPN, или отправьте на Email.",
                reply_markup=gift_sent_kb(gift_card.code),
                parse_mode="HTML",
            )
            success = True
        else:
            success = False
    elif inv.is_gift and inv.gift_for_user_id:
        recipient = await crud.get_user(inv.gift_for_user_id)
        if recipient and recipient.email:
            success = await deliver_gift(inv.user_id, inv.days, recipient_email=recipient.email)
        else:
            success = await deliver_vpn(call.bot, inv.gift_for_user_id, inv.days, is_gift=True)
    else:
        success = await deliver_vpn(call.bot, recipient_id, inv.days, is_gift=inv.is_gift)
    if success:
        await call.message.edit_text(
            f"✅ Счёт {invoice_id} подтверждён.\n"
            f"{'Подарочная ссылка отправлена покупателю.' if inv.is_gift else f'VPN выдан пользователю {recipient_id}.'}",
            parse_mode="HTML",
        )
        await call.answer("Платёж подтверждён")
    else:
        await call.answer("Ошибка выдачи VPN", show_alert=True)


# ─── Broadcast ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminState.broadcast)
    await call.message.edit_text(
        "📤 <b>Рассылка</b>\n\nОтправьте сообщение для рассылки всем пользователям:",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(AdminState.broadcast)
async def process_broadcast(message: Message, state: FSMContext) -> None:
    await state.clear()
    users = await crud.get_all_users()
    ok, fail = 0, 0
    for user in users:
        try:
            await message.copy_to(user.user_id)
            ok += 1
        except Exception:
            fail += 1
    await message.answer(f"📤 Рассылка завершена!\n✅ Отправлено: {ok}\n❌ Ошибок: {fail}")


# ─── Gift create ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:gift_create")
async def adm_gift_create_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminState.wait_for_gift_days)
    await call.message.edit_text("🎁 Введите количество дней для подарочной карты:")
    await call.answer()


@router.message(AdminState.wait_for_gift_days)
async def process_gift_days(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число")
        return
    gc = await crud.create_gift_card(message.from_user.id, days)
    bot_info = await message.bot.get_me()
    from bot.keyboards.inline import share_gift_kb
    await message.answer(
        f"🎁 <b>Подарочная карта создана!</b>\n\n"
        f"📅 Дней: <b>{gc.days}</b>\n"
        f"🔑 Код: <code>{gc.code}</code>\n\n"
        f"Поделитесь ссылкой: https://t.me/{bot_info.username}?start=gift_{gc.code}",
        reply_markup=share_gift_kb(gc.code, gc.days, bot_info.username, message.from_user.first_name or "Admin"),
        parse_mode="HTML",
    )


# ─── Withdrawal decisions ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:w_approve:"))
async def adm_w_approve(call: CallbackQuery) -> None:
    w_id = int(call.data.split(":")[2])
    wr = await crud.get_withdrawal(w_id)
    if not wr:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    await crud.update_withdrawal_status(w_id, "approved")
    try:
        await call.bot.send_message(wr.user_id, f"✅ Ваша заявка на вывод #{w_id} одобрена! Средства будут переведены в течение 24 часов.")
    except Exception:
        pass
    await call.message.edit_text(f"✅ Заявка #{w_id} одобрена.")
    await call.answer()


@router.callback_query(F.data.startswith("adm:w_reject:"))
async def adm_w_reject(call: CallbackQuery) -> None:
    w_id = int(call.data.split(":")[2])
    wr = await crud.get_withdrawal(w_id)
    if not wr:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    await crud.update_withdrawal_status(w_id, "rejected")
    try:
        await call.bot.send_message(wr.user_id, f"❌ Ваша заявка на вывод #{w_id} отклонена. Обратитесь в поддержку.")
    except Exception:
        pass
    await call.message.edit_text(f"❌ Заявка #{w_id} отклонена.")
    await call.answer()


# ─── Support reply ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("support_reply:"))
async def adm_support_reply_start(call: CallbackQuery, state: FSMContext) -> None:
    uid = int(call.data.split(":")[1])
    await state.update_data(reply_to=uid)
    await state.set_state(SupportState.wait_for_reply)
    await call.message.reply(f"💬 Введите ответ пользователю {uid}:")
    await call.answer()


@router.message(SupportState.wait_for_reply)
async def process_support_reply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    uid = data.get("reply_to")
    if not uid:
        return
    try:
        await message.bot.send_message(
            uid,
            Texts.SUPPORT_REPLY_HEADER + html.escape(message.text or ""),
            parse_mode="HTML",
        )
        await message.answer("✅ Ответ отправлен!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# ─── noop ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()
