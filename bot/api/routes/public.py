from __future__ import annotations

import base64
import logging
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from bot.config import settings
from bot.database import crud
from bot.services import vpn as vpn_service
from bot.services.vpn import normalize_client_payload

logger = logging.getLogger(__name__)
router = APIRouter()

DARK_STYLE = """
body { background: #0f0f23; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; margin: 0; padding: 20px; }
.card { background: #1a1a3e; border-radius: 12px; padding: 20px; margin: 10px 0; }
h1 { color: #7c83fd; }
h2 { color: #a0a8ff; }
.badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 0.85em; }
.badge-ok { background: #1e5c2e; color: #4caf50; }
.badge-warn { background: #5c4a1e; color: #ff9800; }
table { width: 100%; border-collapse: collapse; }
td, th { padding: 8px 12px; border-bottom: 1px solid #333; }
th { color: #a0a8ff; }
a { color: #7c83fd; }
"""


@router.get("/sub/{sub_token}")
async def proxy_subscription(sub_token: str) -> PlainTextResponse:
    """
    Aggregated subscription endpoint.

    Collects proxy links from ALL configured 3X-UI servers for the user's
    vpn_name (email), merges them, base64-encodes and returns a standard
    subscription response that Hiddify / v2rayNG / Streisand can import.
    """
    user = await crud.get_user_by_sub_token(sub_token)
    if not user or not user.vpn_name:
        raise HTTPException(status_code=404, detail="Subscription not found")

    # ── Collect links from all servers ────────────────────────────────────────
    try:
        all_links = await vpn_service.get_all_links(user.vpn_name)
    except Exception as exc:
        logger.error("Subscription aggregate error for %s: %s", sub_token, exc)
        raise HTTPException(status_code=503, detail="VPN servers unavailable")

    if not all_links:
        # Fallback: return empty valid subscription (don't 404 — client may retry)
        logger.warning("No links for %s (vpn_name=%s)", sub_token, user.vpn_name)
        all_links = []

    sub_text = "\n".join(all_links)
    encoded = base64.b64encode(sub_text.encode("utf-8")).decode("utf-8")

    # ── Build subscription-userinfo header ────────────────────────────────────
    upload = 0
    download = 0
    total_bytes = 0
    expire_ts = 0

    try:
        raw = await vpn_service.get_client(user.vpn_name)
        info = normalize_client_payload(raw)
        upload = info.get("upload_bytes") or 0
        download = info.get("download_bytes") or 0
        limit_gb = info.get("traffic_limit_gb") or 0
        total_bytes = limit_gb * 1_073_741_824 if limit_gb > 0 else 0
        expire_ts = info.get("expire_timestamp") or 0
    except Exception as e:
        logger.debug("Could not fetch traffic info for sub %s: %s", sub_token, e)

    info_header = (
        f"upload={upload}; download={download}; "
        f"total={total_bytes}; expire={expire_ts}"
    )

    return PlainTextResponse(
        encoded,
        headers={
            "subscription-userinfo": info_header,
            "profile-title": "Adoria VPN",
            "profile-update-interval": "12",
            "support-url": f"https://t.me/{settings.bot_username}",
            "Cache-Control": "no-cache, no-store",
        },
    )


@router.get("/info/{sub_token}")
async def user_info_page(sub_token: str) -> HTMLResponse:
    """User-facing info page (HTML)."""
    user = await crud.get_user_by_sub_token(sub_token)
    if not user or not user.vpn_name:
        raise HTTPException(status_code=404, detail="Subscription not found")

    client: dict = {}
    try:
        raw = await vpn_service.get_client(user.vpn_name)
        client = normalize_client_payload(raw)
    except Exception as exc:
        logger.warning("Info page VPN fetch error for %s: %s", sub_token, exc)

    left_days = client.get("left_days", "?")
    expires_at = client.get("expires_at", "?")
    used_bytes = client.get("used_traffic_bytes") or 0
    limit_gb = client.get("traffic_limit_gb") or 0
    used_gb = round(used_bytes / 1_073_741_824, 2)
    limit_str = f"{limit_gb} ГБ" if limit_gb > 0 else "∞"
    used_pct = min(100, int(used_gb / limit_gb * 100)) if limit_gb > 0 else 0
    is_banned = client.get("is_banned", False)
    status_badge = (
        '<span class="badge badge-warn">Заблокирован</span>'
        if is_banned
        else '<span class="badge badge-ok">Активен</span>'
    )

    # Count links across all servers
    try:
        link_count = len(await vpn_service.get_all_links(user.vpn_name))
    except Exception:
        link_count = 0

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Adoria VPN - My Profile</title>
  <style>{DARK_STYLE}</style>
</head>
<body>
  <h1>Adoria VPN</h1>
  <div class="card">
    <h2>Profile: <code>{user.vpn_name}</code></h2>
    <p>Status: {status_badge}</p>
    <table>
      <tr><th>Parameter</th><th>Value</th></tr>
      <tr><td>Days left</td><td><b>{left_days}</b></td></tr>
      <tr><td>Expires at</td><td>{expires_at}</td></tr>
      <tr><td>Traffic used</td><td>{used_gb} GB / {limit_str}</td></tr>
      <tr><td>Available servers</td><td><b>{link_count}</b> proxy endpoints</td></tr>
      {"<tr><td colspan='2'><progress value='" + str(used_pct) + "' max='100' style='width:100%'></progress></td></tr>" if limit_gb > 0 else ""}
    </table>
  </div>
  <div class="card">
    <h2>Subscription Link</h2>
    <p><code>https://{settings.subscription_domain}/sub/{user.sub_token}</code></p>
    <p><a href="https://{settings.site_domain}/dashboard.html">Personal account</a></p>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/health")
async def healthcheck() -> JSONResponse:
    """Quick health probe — also pings all VPN servers."""
    try:
        ping_results = await vpn_service.ping_servers()
    except Exception:
        ping_results = {}
    return JSONResponse({
        "ok": True,
        "service": "Adoria VPN Backend",
        "vpn_servers": ping_results,
    })
