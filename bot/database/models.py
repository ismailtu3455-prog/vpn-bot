from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    vpn_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ref_earned: Mapped[float] = mapped_column(Float, default=0.0)
    partner_ref_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    promo_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    test_taken: Mapped[bool] = mapped_column(Boolean, default=False)
    reminders_sent: Mapped[str] = mapped_column(String(64), default="")
    tos_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    bonus_days_stat: Mapped[int] = mapped_column(Integer, default=0)
    vpn_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    free_reissue_used: Mapped[bool] = mapped_column(Boolean, default=False)
    sub_token: Mapped[str | None] = mapped_column(String(15), unique=True, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    invoices: Mapped[list[Invoice]] = relationship("Invoice", back_populates="user", lazy="dynamic")
    web_sessions: Mapped[list[WebSession]] = relationship("WebSession", back_populates="user", lazy="dynamic")
    otp_codes: Mapped[list[OtpCode]] = relationship("OtpCode", back_populates="user", lazy="dynamic")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), index=True)
    plan_key: Mapped[str] = mapped_column(String(32))
    plan_title: Mapped[str] = mapped_column(String(128))
    days: Mapped[int] = mapped_column(Integer)
    amount_rub: Mapped[float] = mapped_column(Float)
    amount_currency: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), default="RUB")
    gateway: Mapped[str] = mapped_column(String(32))
    invoice_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active, paid, expired, cancelled
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_gift: Mapped[bool] = mapped_column(Boolean, default=False)
    gift_for_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gift_card_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    review_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="invoices")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    days: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Promocode(Base):
    __tablename__ = "promocodes"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    promo_type: Mapped[str] = mapped_column(String(32))  # "days", "discount", "balance"
    value: Mapped[float] = mapped_column(Float)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    uses_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Admin(Base):
    __tablename__ = "admins"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    amount: Mapped[float] = mapped_column(Float)
    details: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending, approved, rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GiftCard(Base):
    __tablename__ = "gift_cards"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    days: Mapped[int] = mapped_column(Integer)
    buyer_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    used_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), index=True)
    code: Mapped[str] = mapped_column(String(6))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship("User", back_populates="otp_codes")


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), index=True)
    token: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship("User", back_populates="web_sessions")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    code: Mapped[str] = mapped_column(String(6))
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
