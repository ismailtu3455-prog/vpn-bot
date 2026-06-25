from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit
from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalize_origin(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path
    path = parsed.path if parsed.netloc else ""
    return urlunsplit((scheme, netloc.rstrip("/"), path.rstrip("/"), "", ""))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str
    admin_ids: str = ""
    db_path: str = "bot.db"

    vpn_api_url: str = "https://vpn.adoria.fun:25626/api"
    vpn_api_token: str = ""

    price_7d: float = 15.0
    price_1m: float = 40.0
    price_3m: float = 100.0

    crypto_pay_token: Optional[str] = None
    crypto_currency: str = "USDT"
    stars_rate: float = 1.35

    yoomoney_wallet: Optional[str] = None
    yoomoney_secret: Optional[str] = None

    lava_shop_id: Optional[str] = None
    lava_api_key: Optional[str] = None

    resend_api_key: Optional[str] = None
    email_from: str = "Adoria VPN <noreply@adoria.fun>"

    api_port: int = 8888
    api_origin: Optional[str] = None
    api_secret: str = "change-this-secret-key-in-production"
    site_domain: str = "cloudv.adoria.fun"
    subscription_domain: str = "cloudv.adoria.fun"
    bot_username: str = "Adoria_funbot"

    proxy_url: Optional[str] = None
    start_mock_vpn_api: bool = False

    @property
    def get_admin_ids(self) -> set[int]:
        if not self.admin_ids:
            return set()
        result: set[int] = set()
        for part in self.admin_ids.split(","):
            part = part.strip()
            if part.isdigit():
                result.add(int(part))
        return result

    @property
    def site_origin(self) -> str:
        return _normalize_origin(self.site_domain)

    @property
    def resolved_api_origin(self) -> str:
        return _normalize_origin(self.api_origin or self.site_origin)

    @property
    def dashboard_url(self) -> str:
        return f"{self.site_origin}/dashboard.html"

    @property
    def yoomoney_webhook_url(self) -> str:
        return f"{self.resolved_api_origin}/api/webhooks/yoomoney"

    @property
    def lava_webhook_url(self) -> str:
        return f"{self.resolved_api_origin}/api/webhooks/lava"


settings = Settings()  # type: ignore[call-arg]

# Dynamic settings loaded from DB at runtime (populated in __main__.py)
db_settings: dict[str, Any] = {
    "crypto_pay_token": None,
    "stars_enabled": "1",
    "yoomoney_wallet": None,
    "yoomoney_secret": None,
    "lava_shop_id": None,
    "lava_api_key": None,
    # Referral settings
    "ref_reward_start": "50",
    "ref_percent_lvl1": "10",
    "ref_percent_lvl2": "5",
    # Test period
    "test_enabled": "1",
    "test_days": "3",
    # VPN limits
    "default_limit_gb": "0",
    # Channels
    "main_channel_id": None,
    "main_channel_url": None,
    "payment_channel_id": None,
    # Misc
    "balance_pay_enabled": "1",
    # Tome (manual SBP)
    "tome_enabled": "0",
    "tome_phone": None,
    "tome_bank": None,
    "usdt_rate": "90",
}
