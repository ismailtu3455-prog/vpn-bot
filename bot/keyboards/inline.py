from __future__ import annotations

from typing import Any

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import Plan, Promocode, Admin, WithdrawalRequest, Invoice


# ─── Navigation ──────────────────────────────────────────────────────────────

def back_kb(callback_data: str = "back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]
    ])


def back_to_buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить VPN", callback_data="buy")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back")],
    ])

def reissue_payment_methods_kb(balance: float, cost: float, db_settings: dict, is_free_available: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_free_available:
        builder.row(InlineKeyboardButton(text="✅ Заменить бесплатно", callback_data="pay_reissue_free"))
    
    if balance >= cost:
        builder.row(InlineKeyboardButton(text=f"💳 С баланса ({balance:.0f}₽)", callback_data=f"pay:balance:reissue"))
    
    if db_settings.get("stars_enabled", "1") == "1":
        builder.row(InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay:stars:reissue"))

    if db_settings.get("crypto_pay_token"):
        builder.row(InlineKeyboardButton(text="₿ CryptoBot", callback_data=f"pay:crypto:reissue"))

    if db_settings.get("yoomoney_wallet"):
        builder.row(InlineKeyboardButton(text="💛 YooMoney", callback_data=f"pay:yoomoney:reissue"))

    if db_settings.get("platega_shop_id") and db_settings.get("platega_api_key"):
        builder.row(InlineKeyboardButton(text="🟢 СБП", callback_data=f"pay:platega:reissue"))

    if db_settings.get("tome_enabled", "0") == "1":
        builder.row(InlineKeyboardButton(text="📱 СБП (вручную)", callback_data=f"pay:tome:reissue"))

    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="my"))
    return builder.as_markup()


# ─── Main menu ────────────────────────────────────────────────────────────────

def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👤 Мой VPN", callback_data="my"),
        InlineKeyboardButton(text="🛒 Купить VPN", callback_data="buy"),
    )
    builder.row(
        InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals"),
        InlineKeyboardButton(text="🎁 Промокод", callback_data="enter_promo"),
    )
    builder.row(
        InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        InlineKeyboardButton(text="💬 Поддержка", callback_data="support"),
    )
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="🛠 Админ панель", callback_data="adm:home")
        )
    return builder.as_markup()


# ─── Invoice ──────────────────────────────────────────────────────────────────

def invoice_kb(pay_url: str, invoice_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check:{invoice_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel:{invoice_id}")],
    ])


def yoomoney_invoice_kb(pay_url: str, invoice_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💛 Оплатить через YooMoney", url=pay_url)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check:{invoice_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel:{invoice_id}")],
    ])


# ─── Client ───────────────────────────────────────────────────────────────────

def client_kb(balance: float = 0.0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Переиздать ключ (50₽)", callback_data="reissue_key"))
    builder.row(InlineKeyboardButton(text="🛒 Продлить подписку", callback_data="buy"))
    builder.row(InlineKeyboardButton(text="💰 Баланс: {:.2f}₽".format(balance), callback_data="my"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back"))
    return builder.as_markup()


def no_client_kb(balance: float = 0.0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить VPN", callback_data="buy")],
        [InlineKeyboardButton(text="🧪 Тест бесплатно", callback_data="take_test")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])

# ─── Mandatory subscription ───────────────────────────────────────────────────

def mandatory_sub_kb(channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=channel_url)],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_mandatory_sub")],
    ])


# ─── Plans ────────────────────────────────────────────────────────────────────

def plans_kb(
    plans: list[Plan],
    has_promo: bool = False,
    prefix: str = "plan",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.row(InlineKeyboardButton(
            text=f"📦 {plan.title} — {plan.price:.0f}₽",
            callback_data=f"{prefix}:{plan.id}",
        ))
    if prefix == "plan":
        builder.row(InlineKeyboardButton(text="🎁 Купить в подарок", callback_data="buy_gift"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back"))
    return builder.as_markup()

def gift_sent_kb(gift_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📧 Отправить на Email", callback_data=f"send_gift_email:{gift_code}")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="back")],
    ])


def payment_methods_kb(
    plan: Plan,
    balance: float,
    price: float,
    is_gift: bool = False,
    db_settings: dict | None = None,
) -> InlineKeyboardMarkup:
    if db_settings is None:
        db_settings = {}
    prefix = "giftpay" if is_gift else "pay"
    plan_id = plan.id
    builder = InlineKeyboardBuilder()

    # Balance
    balance_enabled = db_settings.get("balance_pay_enabled", "1") == "1"
    if balance_enabled and balance >= price:
        builder.row(InlineKeyboardButton(
            text=f"💰 Баланс ({balance:.0f}₽)",
            callback_data=f"{prefix}:balance:{plan_id}",
        ))

    # CryptoBot
    crypto_token = db_settings.get("crypto_pay_token")
    if crypto_token:
        builder.row(InlineKeyboardButton(
            text="₿ CryptoBot (USDT)",
            callback_data=f"{prefix}:crypto:{plan_id}",
        ))

    # Stars
    stars_enabled = db_settings.get("stars_enabled", "1") == "1"
    if stars_enabled:
        builder.row(InlineKeyboardButton(
            text="⭐ Telegram Stars",
            callback_data=f"{prefix}:stars:{plan_id}",
        ))

    # YooMoney
    ym_wallet = db_settings.get("yoomoney_wallet")
    if ym_wallet:
        builder.row(InlineKeyboardButton(
            text="💛 YooMoney / Банковская карта",
            callback_data=f"{prefix}:yoomoney:{plan_id}",
        ))

    # Platega СБП
    platega_shop = db_settings.get("platega_shop_id")
    if platega_shop:
        builder.row(InlineKeyboardButton(
            text="🟢 СБП",
            callback_data=f"{prefix}:platega:{plan_id}",
        ))

    # Tome (manual)
    tome_enabled = db_settings.get("tome_enabled", "0") == "1"
    if tome_enabled:
        builder.row(InlineKeyboardButton(
            text="📱 СБП (вручную)",
            callback_data=f"{prefix}:tome:{plan_id}",
        ))

    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="buy"))
    return builder.as_markup()


# ─── Admin home ───────────────────────────────────────────────────────────────

def admin_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
        InlineKeyboardButton(text="👥 Управление", callback_data="adm:manage"),
    )
    builder.row(
        InlineKeyboardButton(text="💳 Платежи", callback_data="adm:payments"),
        InlineKeyboardButton(text="📦 Тарифы", callback_data="adm:plans"),
    )
    builder.row(
        InlineKeyboardButton(text="🎟 Промокоды", callback_data="adm:promos"),
        InlineKeyboardButton(text="👮 Администраторы", callback_data="adm:admins"),
    )
    builder.row(
        InlineKeyboardButton(text="🔗 Каналы", callback_data="adm:channels_setup"),
        InlineKeyboardButton(text="👥 Реферальная", callback_data="adm:ref_setup"),
    )
    builder.row(
        InlineKeyboardButton(text="🧪 Тест период", callback_data="adm:test_setup"),
        InlineKeyboardButton(text="🖥 VPN Сервер", callback_data="adm:server_status"),
    )
    builder.row(
        InlineKeyboardButton(text="📋 Invoices", callback_data="adm:payinvoice:0"),
        InlineKeyboardButton(text="🕐 На проверку", callback_data="adm:payreview:0"),
    )
    builder.row(
        InlineKeyboardButton(text="📤 Рассылка", callback_data="adm:broadcast"),
    )
    return builder.as_markup()


def admin_manage_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="adm:find_user")],
        [InlineKeyboardButton(text="🎁 Создать подарочную карту", callback_data="adm:gift_create")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home")],
    ])


def admin_channels_setup_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Основной канал", callback_data="adm:set_main_channel")],
        [InlineKeyboardButton(text="💳 Канал платежей", callback_data="adm:set_pay_channel")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home")],
    ])


def admin_channel_setup_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🤖 Добавить бота в канал",
            url=f"https://t.me/{bot_username}?startchannel=true",
        )],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:channels_setup")],
    ])


def admin_list_kb(admins: list[Admin]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for admin in admins:
        builder.row(InlineKeyboardButton(
            text=f"❌ {admin.user_id}",
            callback_data=f"adm:del_admin:{admin.user_id}",
        ))
    builder.row(InlineKeyboardButton(text="➕ Добавить", callback_data="adm:add_admin"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home"))
    return builder.as_markup()


def plans_list_kb(plans: list[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.row(InlineKeyboardButton(
            text=f"❌ {plan.title} ({plan.days}д, {plan.price}₽)",
            callback_data=f"adm:del_plan:{plan.id}",
        ))
    builder.row(InlineKeyboardButton(text="➕ Добавить тариф", callback_data="adm:add_plan"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home"))
    return builder.as_markup()


def promos_list_kb(promos: list[Promocode]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for promo in promos:
        status = "✅" if promo.is_active else "❌"
        builder.row(InlineKeyboardButton(
            text=f"{status} {promo.code} ({promo.promo_type}, {promo.uses_count}/{promo.max_uses})",
            callback_data=f"adm:del_promo:{promo.code}",
        ))
    builder.row(InlineKeyboardButton(text="➕ Добавить промокод", callback_data="adm:add_promo"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home"))
    return builder.as_markup()


def admin_test_setup_kb(db_s: dict) -> InlineKeyboardMarkup:
    test_on = db_s.get("test_enabled", "1") == "1"
    test_days = db_s.get("test_days", "3")
    limit_gb = db_s.get("default_limit_gb", "0")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'✅' if test_on else '❌'} Тест период",
            callback_data="adm:toggle_test",
        )],
        [InlineKeyboardButton(text=f"📅 Дней: {test_days}", callback_data="adm:set_test_days")],
        [InlineKeyboardButton(text=f"📊 Лимит GB: {limit_gb}", callback_data="adm:set_limit")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home")],
    ])


def promo_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Дни", callback_data="promo_type:days")],
        [InlineKeyboardButton(text="💰 Скидка %", callback_data="promo_type:discount")],
        [InlineKeyboardButton(text="💵 Баланс", callback_data="promo_type:balance")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:promos")],
    ])


def admin_ref_setup_kb(db_s: dict) -> InlineKeyboardMarkup:
    start = db_s.get("ref_reward_start", "50")
    lvl1 = db_s.get("ref_percent_lvl1", "10")
    lvl2 = db_s.get("ref_percent_lvl2", "5")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎁 Бонус за старт: {start}₽", callback_data="adm:set_ref_start")],
        [InlineKeyboardButton(text=f"📊 Уровень 1: {lvl1}%", callback_data="adm:set_ref_lvl1")],
        [InlineKeyboardButton(text=f"📊 Уровень 2: {lvl2}%", callback_data="adm:set_ref_lvl2")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home")],
    ])


def admin_payments_kb(db_s: dict) -> InlineKeyboardMarkup:
    crypto_token = db_s.get("crypto_pay_token")
    stars_on = db_s.get("stars_enabled", "1") == "1"
    ym_wallet = db_s.get("yoomoney_wallet")
    platega_shop = db_s.get("platega_shop_id")
    tome_on = db_s.get("tome_enabled", "0") == "1"
    balance_on = db_s.get("balance_pay_enabled", "1") == "1"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"₿ CryptoBot: {'✅' if crypto_token else '❌'}",
        callback_data="adm:pay_setup:crypto",
    ))
    builder.row(InlineKeyboardButton(
        text=f"⭐ Stars: {'✅' if stars_on else '❌'}",
        callback_data="adm:pay_setup:stars",
    ))
    builder.row(InlineKeyboardButton(
        text=f"💛 YooMoney: {'✅ ' + str(ym_wallet)[:6] + '...' if ym_wallet else '❌'}",
        callback_data="adm:pay_setup:yoomoney",
    ))
    builder.row(InlineKeyboardButton(
        text=f"🟢 Platega СБП: {'✅ ' + str(platega_shop)[:8] + '...' if platega_shop else '❌'}",
        callback_data="adm:pay_setup:platega",
    ))
    builder.row(InlineKeyboardButton(
        text=f"📱 СБП (tome): {'✅' if tome_on else '❌'}",
        callback_data="adm:pay_setup:tome",
    ))
    builder.row(InlineKeyboardButton(
        text=f"💰 Оплата балансом: {'✅' if balance_on else '❌'}",
        callback_data="adm:toggle_balance_pay",
    ))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home"))
    return builder.as_markup()


def user_manage_kb(user_id: int, is_vpn_banned: bool = False) -> InlineKeyboardMarkup:
    ban_text = "✅ Разбан VPN" if is_vpn_banned else "🚫 Бан VPN"
    ban_cb = f"adm:unban_vpn:{user_id}" if is_vpn_banned else f"adm:ban_vpn:{user_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data=f"adm:add_balance:{user_id}")],
        [InlineKeyboardButton(text="🎫 Выдать VPN", callback_data=f"adm:grant_vpn:{user_id}")],
        [InlineKeyboardButton(text=ban_text, callback_data=ban_cb)],
        [InlineKeyboardButton(text="🗑 Удалить пользователя", callback_data=f"adm:del_user:{user_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:manage")],
    ])


def admin_vpn_actions_kb(user_id: int, is_vpn_banned: bool = False) -> InlineKeyboardMarkup:
    ban_text = "✅ Разблокировать VPN" if is_vpn_banned else "🚫 Заблокировать VPN"
    ban_cb = f"adm:unban_vpn:{user_id}" if is_vpn_banned else f"adm:ban_vpn:{user_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ban_text, callback_data=ban_cb)],
        [InlineKeyboardButton(text="🗑 Удалить VPN ключ", callback_data=f"adm:del_vpn:{user_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm:find_user")],
    ])


def admin_server_status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Трафик", callback_data="adm:vpn_stats")],
        [InlineKeyboardButton(text="📋 Логи", callback_data="adm:vpn_logs")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="adm:server_status")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home")],
    ])


def withdrawal_decision_kb(w_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm:w_approve:{w_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm:w_reject:{w_id}"),
        ]
    ])


def share_gift_kb(
    gift_code: str,
    days: int,
    bot_username: str,
    buyer_name: str,
) -> InlineKeyboardMarkup:
    share_url = (
        f"https://t.me/share/url?url=https://t.me/{bot_username}?start%3Dgift_{gift_code}"
        f"&text=🎁+Держи+подарок+{days}+дней+VPN+от+{buyer_name}!"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться подарком", url=share_url)],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back")],
    ])


def invoices_list_kb(
    invoices: list[Invoice],
    page: int,
    sort_mode: str,
    has_search: bool,
    total: int = 0,
    per_page: int = 10,
    mode: str = "all",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    page_cb = "adm:payreview" if mode == "review" else "adm:payinvoice"
    target_mode = "review" if mode == "review" else "all"
    for inv in invoices:
        status_icon = {"paid": "✅", "active": "⏳", "expired": "❌", "cancelled": "🚫"}.get(inv.status, "❓")
        builder.row(InlineKeyboardButton(
            text=f"{status_icon} #{inv.id} | {inv.gateway} | {inv.amount_rub:.0f}₽ | {inv.status}",
            callback_data=f"adm:inv_detail:{inv.id}:{target_mode}",
        ))
    # Pagination
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{page_cb}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{max(1, (total + per_page - 1) // per_page)}", callback_data="noop"))
    if (page + 1) * per_page < total:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{page_cb}:{page + 1}"))
    if nav_row:
        builder.row(*nav_row)
    if mode == "review":
        builder.row(InlineKeyboardButton(text="📋 Все счета", callback_data="adm:payinvoice:0"))
    else:
        builder.row(InlineKeyboardButton(text="🕐 Только на проверку", callback_data="adm:payreview:0"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home"))
    return builder.as_markup()
