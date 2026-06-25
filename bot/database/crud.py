from __future__ import annotations

import logging
import random
import secrets
import string
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, func, update, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.database.models import (
    Base, User, Invoice, Setting, Plan, Promocode,
    Admin, WithdrawalRequest, GiftCard, OtpCode, WebSession,
)
from bot.config import settings, db_settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path}",
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
SUB_TOKEN_LENGTH = 15


def generate_sub_token() -> str:
    """Generate a public-safe alphanumeric subscription token."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(SUB_TOKEN_LENGTH))


async def regenerate_sub_token(user_id: int) -> str | None:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            return None

        while True:
            token = generate_sub_token()
            exists = await session.execute(
                select(User.user_id).where(User.sub_token == token, User.user_id != user_id)
            )
            if exists.scalar_one_or_none() is None:
                user.sub_token = token
                await session.commit()
                return token


async def init_db() -> None:
    """Initialize database, run migrations, create default plans."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Run migrations for new columns
    import aiosqlite
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row

        # users table migrations
        existing_cols: set[str] = set()
        async with db.execute("PRAGMA table_info(users)") as cur:
            rows = await cur.fetchall()
            existing_cols = {r["name"] for r in rows}

        migrations = {
            "ref_earned": "ALTER TABLE users ADD COLUMN ref_earned REAL DEFAULT 0.0",
            "partner_ref_id": "ALTER TABLE users ADD COLUMN partner_ref_id INTEGER",
            "promo_used": "ALTER TABLE users ADD COLUMN promo_used TEXT",
            "test_taken": "ALTER TABLE users ADD COLUMN test_taken INTEGER DEFAULT 0",
            "reminders_sent": "ALTER TABLE users ADD COLUMN reminders_sent TEXT DEFAULT ''",
            "tos_accepted": "ALTER TABLE users ADD COLUMN tos_accepted INTEGER DEFAULT 0",
            "bonus_days_stat": "ALTER TABLE users ADD COLUMN bonus_days_stat INTEGER DEFAULT 0",
            "vpn_banned": "ALTER TABLE users ADD COLUMN vpn_banned INTEGER DEFAULT 0",
            "balance": "ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0.0",
            "vpn_name": "ALTER TABLE users ADD COLUMN vpn_name TEXT",
            "ref_id": "ALTER TABLE users ADD COLUMN ref_id INTEGER",
            "email": "ALTER TABLE users ADD COLUMN email TEXT",
            "free_reissue_used": "ALTER TABLE users ADD COLUMN free_reissue_used INTEGER DEFAULT 0",
            "sub_token": "ALTER TABLE users ADD COLUMN sub_token TEXT",
        }
        for col, sql in migrations.items():
            if col not in existing_cols:
                try:
                    await db.execute(sql)
                    logger.info(f"Migration applied: {col}")
                except Exception as e:
                    logger.debug(f"Migration skipped ({col}): {e}")

        # invoices table migrations
        inv_cols: set[str] = set()
        try:
            async with db.execute("PRAGMA table_info(invoices)") as cur:
                rows = await cur.fetchall()
                inv_cols = {r["name"] for r in rows}
        except Exception:
            pass

        inv_migrations = {
            "label": "ALTER TABLE invoices ADD COLUMN label TEXT",
            "is_gift": "ALTER TABLE invoices ADD COLUMN is_gift INTEGER DEFAULT 0",
            "gift_for_user_id": "ALTER TABLE invoices ADD COLUMN gift_for_user_id INTEGER",
            "gift_card_code": "ALTER TABLE invoices ADD COLUMN gift_card_code TEXT",
            "payer_name": "ALTER TABLE invoices ADD COLUMN payer_name TEXT",
            "review_requested": "ALTER TABLE invoices ADD COLUMN review_requested INTEGER DEFAULT 0",
            "review_requested_at": "ALTER TABLE invoices ADD COLUMN review_requested_at DATETIME",
            "paid_at": "ALTER TABLE invoices ADD COLUMN paid_at DATETIME",
        }
        for col, sql in inv_migrations.items():
            if col not in inv_cols:
                try:
                    await db.execute(sql)
                    logger.info(f"Invoice migration applied: {col}")
                except Exception as e:
                    logger.debug(f"Invoice migration skipped ({col}): {e}")

        await db.commit()

    # Create default plans if none exist
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.sub_token.is_(None)))
        users_without_token = result.scalars().all()
        used_tokens = {
            token for token in (
                await session.execute(select(User.sub_token).where(User.sub_token.isnot(None)))
            ).scalars().all()
            if token
        }
        for user in users_without_token:
            token = generate_sub_token()
            while token in used_tokens:
                token = generate_sub_token()
            used_tokens.add(token)
            user.sub_token = token
        if users_without_token:
            await session.commit()
            logger.info("Generated sub_token for %s users", len(users_without_token))

        result = await session.execute(select(func.count()).select_from(Plan))
        count = result.scalar() or 0
        if count == 0:
            default_plans = [
                Plan(id="7d", title="7 дней", days=7, price=settings.price_7d),
                Plan(id="1m", title="1 месяц", days=30, price=settings.price_1m),
                Plan(id="3m", title="3 месяца", days=90, price=settings.price_3m),
            ]
            session.add_all(default_plans)
            await session.commit()
            logger.info("Default plans created")

        # Load db_settings from DB
        result = await session.execute(select(Setting))
        settings_rows = result.scalars().all()
        for row in settings_rows:
            db_settings[row.key] = row.value
        logger.info(f"Loaded {len(settings_rows)} settings from DB")


# ─── User ───────────────────────────────────────────────────────────────────

async def register_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    language_code: str | None = None,
    ref_id: int | None = None,
) -> User:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            user = User(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
                ref_id=ref_id,
                sub_token=generate_sub_token(),
            )
            session.add(user)
            while True:
                try:
                    await session.commit()
                    break
                except Exception:
                    await session.rollback()
                    user.sub_token = generate_sub_token()
                    session.add(user)
            await session.refresh(user)
        else:
            # Update dynamic fields
            changed = False
            if username is not None and user.username != username:
                user.username = username
                changed = True
            if first_name is not None and user.first_name != first_name:
                user.first_name = first_name
                changed = True
            if last_name is not None and user.last_name != last_name:
                user.last_name = last_name
                changed = True
            if changed:
                await session.commit()
                await session.refresh(user)
        return user


async def get_user(user_id: int) -> User | None:
    async with AsyncSessionLocal() as session:
        return await session.get(User, user_id)


async def get_user_by_id_or_username(query: str) -> User | None:
    async with AsyncSessionLocal() as session:
        if query.lstrip("-").isdigit():
            result = await session.get(User, int(query))
            return result
        clean = query.lstrip("@").lower()
        result = await session.execute(
            select(User).where(func.lower(User.username) == clean)
        )
        return result.scalar_one_or_none()


async def get_all_users() -> list[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User))
        return list(result.scalars().all())


async def update_user_ref_id(user_id: int, ref_id: int) -> None:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user and user.ref_id is None:
            user.ref_id = ref_id
            await session.commit()


async def delete_user(user_id: int) -> None:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            await session.delete(user)
            await session.commit()


async def set_vpn_name(user_id: int, vpn_name: str | None) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.user_id == user_id).values(vpn_name=vpn_name)
        )
        await session.commit()


async def add_user_balance(user_id: int, amount: float) -> None:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.balance = round((user.balance or 0.0) + amount, 2)
            await session.commit()


async def update_user_promo(user_id: int, code: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.user_id == user_id).values(promo_used=code)
        )
        await session.commit()


async def clear_user_promo(user_id: int) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.user_id == user_id).values(promo_used=None)
        )
        await session.commit()


async def update_user_test_taken(user_id: int) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.user_id == user_id).values(test_taken=True)
        )
        await session.commit()


async def update_user_reminders(user_id: int, reminders_sent: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.user_id == user_id).values(reminders_sent=reminders_sent)
        )
        await session.commit()


async def update_user_tos_accepted(user_id: int) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.user_id == user_id).values(tos_accepted=True)
        )
        await session.commit()


async def set_user_vpn_banned(user_id: int, banned: bool) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.user_id == user_id).values(vpn_banned=banned)
        )
        await session.commit()


# ─── Settings ───────────────────────────────────────────────────────────────

async def get_setting(key: str, default: Any = None) -> Any:
    async with AsyncSessionLocal() as session:
        row = await session.get(Setting, key)
        if row is None:
            return default
        return row.value


async def set_setting(key: str, value: Any) -> None:
    async with AsyncSessionLocal() as session:
        row = await session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=str(value) if value is not None else None)
            session.add(row)
        else:
            row.value = str(value) if value is not None else None
        await session.commit()
    db_settings[key] = str(value) if value is not None else None


update_setting = set_setting  # alias


# ─── Invoices ───────────────────────────────────────────────────────────────

async def create_invoice(
    user_id: int,
    plan_key: str,
    plan_title: str,
    days: int,
    amount_rub: float,
    gateway: str,
    invoice_id: str | None = None,
    amount_currency: float = 0.0,
    currency: str = "RUB",
    label: str | None = None,
    is_gift: bool = False,
    gift_for_user_id: int | None = None,
) -> Invoice:
    async with AsyncSessionLocal() as session:
        inv = Invoice(
            user_id=user_id,
            plan_key=plan_key,
            plan_title=plan_title,
            days=days,
            amount_rub=amount_rub,
            amount_currency=amount_currency,
            currency=currency,
            gateway=gateway,
            invoice_id=invoice_id,
            status="active",
            label=label,
            is_gift=is_gift,
            gift_for_user_id=gift_for_user_id,
        )
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
        return inv


async def get_invoice(invoice_id: str) -> Invoice | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice).where(Invoice.invoice_id == invoice_id)
        )
        return result.scalar_one_or_none()


async def get_invoice_by_label(label: str) -> Invoice | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice).where(Invoice.label == label)
        )
        return result.scalar_one_or_none()


async def get_invoice_by_db_id(db_id: int) -> Invoice | None:
    async with AsyncSessionLocal() as session:
        return await session.get(Invoice, db_id)


async def update_invoice_status(invoice_id: str, status: str) -> None:
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow() if status == "paid" else None
        vals: dict[str, Any] = {"status": status}
        if now is not None:
            vals["paid_at"] = now
            vals["review_requested"] = False
        await session.execute(
            update(Invoice).where(Invoice.invoice_id == invoice_id).values(**vals)
        )
        await session.commit()


async def get_active_invoices() -> list[Invoice]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice).where(Invoice.status == "active")
        )
        return list(result.scalars().all())


async def get_active_invoices_filtered(gateway: str) -> list[Invoice]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice).where(
                and_(Invoice.status == "active", Invoice.gateway == gateway)
            )
        )
        return list(result.scalars().all())


async def get_all_invoices_paginated(
    page: int = 0,
    per_page: int = 20,
    status: str | None = None,
    user_id: int | None = None,
) -> tuple[list[Invoice], int]:
    async with AsyncSessionLocal() as session:
        query = select(Invoice)
        count_query = select(func.count()).select_from(Invoice)

        conditions = []
        if status:
            conditions.append(Invoice.status == status)
        if user_id:
            conditions.append(Invoice.user_id == user_id)

        if conditions:
            query = query.where(and_(*conditions))
            count_query = count_query.where(and_(*conditions))

        total = (await session.execute(count_query)).scalar() or 0
        result = await session.execute(
            query.order_by(Invoice.created_at.desc())
            .offset(page * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total


async def request_invoice_manual_review(invoice_id: str, payer_name: str) -> Invoice | None:
    async with AsyncSessionLocal() as session:
        invoice_result = await session.execute(
            select(Invoice).where(Invoice.invoice_id == invoice_id)
        )
        invoice = invoice_result.scalar_one_or_none()
        if invoice is None:
            return None
        invoice.payer_name = payer_name.strip()
        invoice.review_requested = True
        invoice.review_requested_at = datetime.utcnow()
        await session.commit()
        await session.refresh(invoice)
        return invoice


async def get_review_invoices_paginated(
    page: int = 0,
    per_page: int = 20,
) -> tuple[list[Invoice], int]:
    async with AsyncSessionLocal() as session:
        conditions = and_(Invoice.review_requested.is_(True), Invoice.status == "active")
        total = (
            await session.execute(
                select(func.count()).select_from(Invoice).where(conditions)
            )
        ).scalar() or 0
        result = await session.execute(
            select(Invoice)
            .where(conditions)
            .order_by(Invoice.review_requested_at.desc(), Invoice.created_at.desc())
            .offset(page * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total


async def get_or_create_invoice_gift_card(invoice_id: str, buyer_user_id: int, days: int) -> GiftCard | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice).where(Invoice.invoice_id == invoice_id)
        )
        invoice = result.scalar_one_or_none()
        if invoice is None:
            return None

        if invoice.gift_card_code:
            existing = await session.get(GiftCard, invoice.gift_card_code)
            if existing:
                return existing

        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=12))
        gift_card = GiftCard(code=code, days=days, buyer_user_id=buyer_user_id)
        session.add(gift_card)
        invoice.gift_card_code = code
        await session.commit()
        await session.refresh(gift_card)
        return gift_card


# ─── Stats ──────────────────────────────────────────────────────────────────

async def get_dashboard_stats() -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        total_users = (await session.execute(select(func.count()).select_from(User))).scalar() or 0
        total_invoices = (await session.execute(select(func.count()).select_from(Invoice).where(Invoice.status == "paid"))).scalar() or 0
        revenue = (await session.execute(select(func.sum(Invoice.amount_rub)).where(Invoice.status == "paid"))).scalar() or 0.0
        active_invoices = (await session.execute(select(func.count()).select_from(Invoice).where(Invoice.status == "active"))).scalar() or 0
        return {
            "total_users": total_users,
            "paid_invoices": total_invoices,
            "revenue": round(revenue, 2),
            "active_invoices": active_invoices,
        }


async def get_dashboard_stats_extended() -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        base = await get_dashboard_stats()
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        new_users_today = (await session.execute(
            select(func.count()).select_from(User).where(User.created_at >= today)
        )).scalar() or 0
        revenue_today = (await session.execute(
            select(func.sum(Invoice.amount_rub)).where(
                and_(Invoice.status == "paid", Invoice.paid_at >= today)
            )
        )).scalar() or 0.0
        total_users_with_vpn = (await session.execute(
            select(func.count()).select_from(User).where(User.vpn_name.isnot(None))
        )).scalar() or 0
        base.update({
            "new_users_today": new_users_today,
            "revenue_today": round(revenue_today, 2),
            "users_with_vpn": total_users_with_vpn,
        })
        return base


async def get_referrals_count(user_id: int) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count()).select_from(User).where(User.ref_id == user_id)
        )
        return result.scalar() or 0


async def get_paid_invoices_count(user_id: int) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count()).select_from(Invoice).where(
                and_(Invoice.user_id == user_id, Invoice.status == "paid")
            )
        )
        return result.scalar() or 0


async def get_referral_tree(user_id: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.ref_id == user_id).order_by(User.created_at.desc())
        )
        refs = result.scalars().all()
        items = []
        for u in refs:
            paid = (await session.execute(
                select(func.count()).select_from(Invoice).where(
                    and_(Invoice.user_id == u.user_id, Invoice.status == "paid")
                )
            )).scalar() or 0
            items.append({
                "user_id": u.user_id,
                "username": u.username,
                "first_name": u.first_name,
                "paid_invoices": paid,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            })
        return {"total": len(items), "users": items}


# ─── Admins ──────────────────────────────────────────────────────────────────

async def get_admins() -> list[Admin]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Admin))
        return list(result.scalars().all())


async def add_admin(user_id: int) -> None:
    async with AsyncSessionLocal() as session:
        existing = await session.get(Admin, user_id)
        if not existing:
            session.add(Admin(user_id=user_id))
            await session.commit()


async def remove_admin(user_id: int) -> None:
    async with AsyncSessionLocal() as session:
        row = await session.get(Admin, user_id)
        if row:
            await session.delete(row)
            await session.commit()


# ─── Plans ───────────────────────────────────────────────────────────────────

async def get_all_plans(include_inactive: bool = False) -> list[Plan]:
    async with AsyncSessionLocal() as session:
        query = select(Plan)
        if not include_inactive:
            query = query.where(Plan.is_active == True)
        result = await session.execute(query)
        return list(result.scalars().all())


async def get_plan(plan_id: str) -> Plan | None:
    async with AsyncSessionLocal() as session:
        return await session.get(Plan, plan_id)


async def create_plan(plan_id: str, title: str, days: int, price: float) -> Plan:
    async with AsyncSessionLocal() as session:
        plan = Plan(id=plan_id, title=title, days=days, price=price)
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        return plan


async def delete_plan(plan_id: str) -> None:
    async with AsyncSessionLocal() as session:
        plan = await session.get(Plan, plan_id)
        if plan:
            await session.delete(plan)
            await session.commit()


# ─── Promocodes ──────────────────────────────────────────────────────────────

async def get_promocode(code: str) -> Promocode | None:
    async with AsyncSessionLocal() as session:
        return await session.get(Promocode, code.upper())


async def create_promocode(
    code: str,
    promo_type: str,
    value: float,
    max_uses: int = 1,
) -> Promocode:
    async with AsyncSessionLocal() as session:
        promo = Promocode(
            code=code.upper(),
            promo_type=promo_type,
            value=value,
            max_uses=max_uses,
        )
        session.add(promo)
        await session.commit()
        await session.refresh(promo)
        return promo


async def increment_promo_uses(code: str) -> None:
    async with AsyncSessionLocal() as session:
        promo = await session.get(Promocode, code.upper())
        if promo:
            promo.uses_count += 1
            if promo.uses_count >= promo.max_uses:
                promo.is_active = False
            await session.commit()


async def get_all_promocodes() -> list[Promocode]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Promocode))
        return list(result.scalars().all())


async def delete_promocode(code: str) -> None:
    async with AsyncSessionLocal() as session:
        promo = await session.get(Promocode, code.upper())
        if promo:
            await session.delete(promo)
            await session.commit()


# ─── Referrals / Bonuses ─────────────────────────────────────────────────────

async def process_referral_bonus(
    ref_id: int,
    amount: float,
    level: int = 1,
) -> None:
    pct_key = f"ref_percent_lvl{level}"
    pct = float(db_settings.get(pct_key) or "10") / 100.0
    bonus = round(amount * pct, 2)
    if bonus > 0:
        await add_user_balance(ref_id, bonus)
        async with AsyncSessionLocal() as session:
            user = await session.get(User, ref_id)
            if user:
                user.ref_earned = round((user.ref_earned or 0.0) + bonus, 2)
                await session.commit()


async def add_bonus_days_stat(user_id: int, days: int) -> None:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.bonus_days_stat = (user.bonus_days_stat or 0) + days
            await session.commit()


# ─── Withdrawals ─────────────────────────────────────────────────────────────

async def create_withdrawal(user_id: int, amount: float, details: str) -> WithdrawalRequest:
    async with AsyncSessionLocal() as session:
        wr = WithdrawalRequest(user_id=user_id, amount=amount, details=details)
        session.add(wr)
        await session.commit()
        await session.refresh(wr)
        return wr


async def update_withdrawal_status(w_id: int, status: str) -> None:
    async with AsyncSessionLocal() as session:
        wr = await session.get(WithdrawalRequest, w_id)
        if wr:
            wr.status = status
            wr.processed_at = datetime.utcnow()
            await session.commit()


async def get_withdrawal(w_id: int) -> WithdrawalRequest | None:
    async with AsyncSessionLocal() as session:
        return await session.get(WithdrawalRequest, w_id)


# ─── Gift Cards ───────────────────────────────────────────────────────────────

async def create_gift_card(buyer_user_id: int, days: int) -> GiftCard:
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=12))
    async with AsyncSessionLocal() as session:
        gc = GiftCard(code=code, days=days, buyer_user_id=buyer_user_id)
        session.add(gc)
        await session.commit()
        await session.refresh(gc)
        return gc


async def get_gift_card(code: str) -> GiftCard | None:
    async with AsyncSessionLocal() as session:
        return await session.get(GiftCard, code.upper())


async def use_gift_card(code: str, user_id: int) -> GiftCard | None:
    async with AsyncSessionLocal() as session:
        gc = await session.get(GiftCard, code.upper())
        if gc and not gc.is_used:
            gc.is_used = True
            gc.used_by_user_id = user_id
            gc.used_at = datetime.utcnow()
            await session.commit()
            return gc
        return None

# --- Email Auth ---------------------------------------------------------------

from bot.database.models import EmailVerification

async def get_user_by_email(email: str) -> User | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

async def set_user_email(user_id: int, email: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(update(User).where(User.user_id == user_id).values(email=email))
        await session.commit()

async def create_email_verification(email: str, code: str, user_id: int | None, expires_in_seconds: int = 300) -> None:
    from datetime import datetime, timedelta
    expires_at = datetime.utcnow() + timedelta(seconds=expires_in_seconds)
    async with AsyncSessionLocal() as session:
        ev = EmailVerification(email=email, code=code, user_id=user_id, expires_at=expires_at)
        session.add(ev)
        await session.commit()

async def verify_email_code(email: str, code: str) -> int | None:
    from datetime import datetime
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailVerification)
            .where(EmailVerification.email == email, EmailVerification.code == code)
            .order_by(EmailVerification.id.desc())
            .limit(1)
        )
        ev = result.scalar_one_or_none()
        if ev and ev.expires_at > datetime.utcnow():
            await session.delete(ev)
            await session.commit()
            return ev.user_id
        return None

# ─── OTP ─────────────────────────────────────────────────────────────────────

async def create_otp(user_id: int, code: str, expires_in_seconds: int = 300) -> OtpCode:
    async with AsyncSessionLocal() as session:
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in_seconds)
        otp = OtpCode(user_id=user_id, code=code, expires_at=expires_at)
        session.add(otp)
        await session.commit()
        await session.refresh(otp)
        return otp


async def get_latest_otp(user_id: int) -> OtpCode | None:
    """Get the latest unused OTP for a user."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(OtpCode)
            .where(
                and_(
                    OtpCode.user_id == user_id,
                    OtpCode.is_used == False,
                    OtpCode.expires_at > datetime.utcnow(),
                )
            )
            .order_by(OtpCode.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def verify_otp(user_id: int, code: str) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(OtpCode).where(
                and_(
                    OtpCode.user_id == user_id,
                    OtpCode.code == code,
                    OtpCode.is_used == False,
                    OtpCode.expires_at > datetime.utcnow(),
                )
            ).order_by(OtpCode.created_at.desc()).limit(1)
        )
        otp = result.scalar_one_or_none()
        if otp is None:
            return False
        otp.is_used = True
        await session.commit()
        return True


# ─── Web Sessions ─────────────────────────────────────────────────────────────

async def create_web_session(user_id: int, token: str, expires_in_hours: int = 24) -> WebSession:
    async with AsyncSessionLocal() as session:
        expires_at = datetime.utcnow() + timedelta(hours=expires_in_hours)
        ws = WebSession(user_id=user_id, token=token, expires_at=expires_at)
        session.add(ws)
        await session.commit()
        await session.refresh(ws)
        return ws


async def get_web_session(token: str) -> WebSession | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WebSession).where(
                and_(
                    WebSession.token == token,
                    WebSession.expires_at > datetime.utcnow(),
                )
            )
        )
        return result.scalar_one_or_none()


async def get_user_by_web_token(token: str) -> int | None:
    web_session = await get_web_session(token)
    return web_session.user_id if web_session else None


async def delete_expired_sessions() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(WebSession).where(WebSession.expires_at <= datetime.utcnow())
        )
        await session.commit()


# ─── VPN Banned Users ────────────────────────────────────────────────────────

async def get_vpn_banned_users() -> list[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.vpn_banned == True)
        )
        return list(result.scalars().all())


# ─── Users paginated ─────────────────────────────────────────────────────────

async def get_users_paginated(
    page: int = 0,
    per_page: int = 20,
    search: str | None = None,
) -> tuple[list[User], int]:
    async with AsyncSessionLocal() as session:
        query = select(User)
        count_q = select(func.count()).select_from(User)
        if search:
            cond = or_(
                User.username.ilike(f"%{search}%"),
                User.first_name.ilike(f"%{search}%"),
            )
            if search.lstrip("-").isdigit():
                cond = or_(cond, User.user_id == int(search))
            query = query.where(cond)
            count_q = count_q.where(cond)
        total = (await session.execute(count_q)).scalar() or 0
        result = await session.execute(
            query.order_by(User.created_at.desc()).offset(page * per_page).limit(per_page)
        )
        return list(result.scalars().all()), total

# --- AuthSession --------------------------------------------------------------

from bot.database.models import AuthSession

async def create_auth_session(session_id: str) -> None:
    async with AsyncSessionLocal() as session:
        auth_session = AuthSession(session_id=session_id)
        session.add(auth_session)
        await session.commit()

async def authenticate_session(session_id: str, user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AuthSession).where(AuthSession.session_id == session_id))
        auth_session = result.scalar_one_or_none()
        if auth_session:
            auth_session.user_id = user_id
            await session.commit()
            return True
        return False

async def get_authenticated_session_user(session_id: str) -> int | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AuthSession).where(AuthSession.session_id == session_id))
        auth_session = result.scalar_one_or_none()
        if auth_session and auth_session.user_id:
            return auth_session.user_id
        return None


async def get_user_by_sub_token(sub_token: str) -> User | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.sub_token == sub_token))
        return result.scalar_one_or_none()
