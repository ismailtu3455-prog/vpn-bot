from __future__ import annotations

"""
3X-UI Multi-Server VPN Service
-------------------------------
Wraps the native 3X-UI Panel REST API (/panel/api/...) for multiple servers.
Each server is a ThreeXUIClient. MultiServerVPN is the top-level facade.

Authentication: Bearer token (from Settings → Security → API Tokens).
SSL: verify=False for IP-based panels (self-signed certs).
"""

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)

# ─── Server registry ──────────────────────────────────────────────────────────
# Each entry: name, base_url (with panel path), api_token, inbound_ids, ssl_verify
# Poland will be added here once new details are provided.
_SERVERS: list[dict] = [
    # {
    #     "name": "poland",
    #     "base_url": "https://31.76.94.243:25634/Xp7djh0j57MaQ0rG",
    #     "api_token": "ytcR2xrpS2M7Gsh9hOfLXD5RjuXgFWJA5WxOvoXTMzV0xw7v",
    #     "inbound_ids": [1, 5],   # active: vless TCP Reality + vless XHTTP Reality
    #     "ssl_verify": False,
    # },
    {
        "name": "germany",
        "base_url": "http://89.40.14.209:25051/cfr5GEAOb8JhteN4",
        "api_token": "b3BCOZ6zlLxbJJ8a4znQMYV4819r1T3HuchUs5T5zShgFE5y",
        "inbound_ids": [1, 2, 3],  # active: vless TCP Reality + hysteria + vless XHTTP Reality
        "ssl_verify": False,
    },
]


# ─── Exceptions ───────────────────────────────────────────────────────────────

class VPNAPIError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _coerce_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return int(float(stripped))
        except ValueError:
            return default
    return default


def _parse_expiry(value: Any) -> tuple[str | None, int | None]:
    if value is None or value == "" or value == 0:
        return None, None
    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10_000_000_000:
            ts //= 1000
        if ts <= 0:
            return None, None
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"), ts
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            ts = int(stripped)
            if ts > 10_000_000_000:
                ts //= 1000
            if ts <= 0:
                return None, None
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"), ts
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
            try:
                dt = datetime.strptime(stripped, fmt)
                ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
                return dt.strftime("%Y-%m-%d %H:%M:%S"), ts
            except ValueError:
                continue
        return stripped, None
    return str(value), None


def normalize_client_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize client fields from 3X-UI API or legacy mock responses."""
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    data = data or {}

    expires_at, expire_timestamp = _parse_expiry(
        data.get("expires_at") or data.get("expire_at") or data.get("expire_timestamp") or data.get("expiryTime")
    )

    upload_bytes = _coerce_int(data.get("up") or data.get("upload_bytes") or data.get("used_upload_bytes"))
    download_bytes = _coerce_int(data.get("down") or data.get("download_bytes") or data.get("used_download_bytes"))
    used_bytes = _coerce_int(data.get("used_traffic_bytes") or data.get("used_bytes"))
    if not used_bytes:
        used_bytes = upload_bytes + download_bytes

    total_raw = data.get("total") or data.get("traffic_limit_bytes") or data.get("limit_bytes")
    limit_bytes = _coerce_int(total_raw)

    limit_gb = data.get("traffic_limit_gb")
    if limit_gb is None:
        limit_gb = round(limit_bytes / 1_073_741_824) if limit_bytes else 0
    else:
        limit_gb = _coerce_int(limit_gb)
        if not limit_bytes and limit_gb > 0:
            limit_bytes = limit_gb * 1_073_741_824

    links_raw = data.get("links") or data.get("vless_links") or []
    if isinstance(links_raw, dict):
        links = [v for v in links_raw.values() if isinstance(v, str) and v.strip()]
    elif isinstance(links_raw, list):
        links = [v for v in links_raw if isinstance(v, str) and v.strip()]
    else:
        links = []

    left_days = _coerce_int(data.get("left_days"), default=-1)
    if left_days < 0 and expire_timestamp:
        left_days = max(0, int((expire_timestamp - datetime.now(timezone.utc).timestamp()) // 86400))
    elif left_days < 0:
        left_days = 0

    return {
        **data,
        "expires_at": expires_at,
        "expire_timestamp": expire_timestamp,
        "left_days": left_days,
        "used_traffic_bytes": _coerce_int(used_bytes),
        "upload_bytes": upload_bytes,
        "download_bytes": download_bytes,
        "traffic_limit_gb": _coerce_int(limit_gb),
        "traffic_limit_bytes": limit_bytes,
        "is_banned": bool(data.get("is_banned") or data.get("banned") or not data.get("enable", True)),
        "subscription_url": data.get("subscription_url") or data.get("sub_url"),
        "vless_url": data.get("vless_url") or data.get("link"),
        "links": links,
    }


# ─── Single-server client ──────────────────────────────────────────────────────

class ThreeXUIClient:
    """
    Async client for one 3X-UI panel instance.

    Uses Bearer token auth on /panel/api/* endpoints.
    All write operations run against the specified inbound_ids.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        api_token: str,
        inbound_ids: list[int],
        ssl_verify: bool = False,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.inbound_ids = inbound_ids
        self.ssl_verify = ssl_verify

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        json: Any = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{self.base_url}/panel/api/{path.lstrip('/')}"
        connector = aiohttp.TCPConnector(ssl=self.ssl_verify)
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.request(
                    method.upper(),
                    url,
                    json=json,
                    params=params,
                    headers=self._headers(),
                ) as resp:
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        text = await resp.text()
                        raise VPNAPIError(
                            f"[{self.name}] Non-JSON ({resp.status}): {text[:200]}",
                            resp.status,
                        )
                    if not resp.ok:
                        msg = data.get("msg") or data.get("message") or data.get("error") or str(data)
                        raise VPNAPIError(f"[{self.name}] HTTP {resp.status}: {msg}", resp.status)
                    # 3X-UI wraps responses in {"success": bool, "msg": ..., "obj": ...}
                    if isinstance(data, dict) and data.get("success") is False:
                        msg = data.get("msg") or data.get("message") or str(data)
                        raise VPNAPIError(f"[{self.name}] API error: {msg}")
                    return data
        except VPNAPIError:
            raise
        except aiohttp.ClientError as e:
            raise VPNAPIError(f"[{self.name}] Connection error: {e}")
        except Exception as e:
            raise VPNAPIError(f"[{self.name}] Unexpected: {e}")

    # ── Client lifecycle ─────────────────────────────────────────────────────

    async def add_client(
        self,
        email: str,
        days: int,
        limit_bytes: int = 0,
        inbound_ids: list[int] | None = None,
    ) -> dict:
        """
        Add client to inbounds via POST /panel/api/clients/add.
        3X-UI v3 API: body = {client: {...}, inboundIds: [...]}.
        All target inbound IDs in one call.
        """
        target_ids = inbound_ids if inbound_ids is not None else self.inbound_ids

        if days > 0:
            expiry_ms = int((time.time() + days * 86400) * 1000)
        else:
            expiry_ms = 0

        body = {
            "client": {
                "email": email,
                "enable": True,
                "expiryTime": expiry_ms,
                "total": limit_bytes,
                "comment": "tg_bot",
            },
            "inboundIds": target_ids,
        }
        result = await self._request("POST", "clients/add", json=body)
        return result

    async def add_client_to_all_inbounds(
        self,
        email: str,
        days: int,
        limit_bytes: int = 0,
    ) -> list[dict]:
        """Add client to ALL configured inbounds in a single API call."""
        try:
            res = await self.add_client(email, days, limit_bytes)
            logger.info(
                f"[{self.name}] Added client {email} to inbounds {self.inbound_ids}: "
                f"{res.get('msg', 'ok')}"
            )
            return [{"inbound_ids": self.inbound_ids, "ok": True, "data": res}]
        except VPNAPIError as e:
            logger.error(
                f"[{self.name}] Failed to add {email} to inbounds {self.inbound_ids}: {e}"
            )
            return [{"inbound_ids": self.inbound_ids, "ok": False, "error": str(e)}]

    async def get_client(self, email: str) -> dict:
        """GET /panel/api/clients/get/{email}"""
        data = await self._request("GET", f"clients/get/{email}")
        # v3: {"success": true, "obj": {"client": {...}, "inboundIds": [...], ...}}
        obj = data.get("obj") or {}
        # Flatten: prefer obj.client, fallback to obj itself
        client_data = obj.get("client") or obj
        # Attach traffic fields if present at obj level
        if "up" not in client_data:
            client_data["up"] = obj.get("up", 0)
            client_data["down"] = obj.get("down", 0)
            client_data["total"] = obj.get("total", 0)
        return client_data

    async def get_client_traffic(self, email: str) -> dict:
        """GET /panel/api/clients/traffic/{email}"""
        data = await self._request("GET", f"clients/traffic/{email}")
        obj = data.get("obj") or {}
        return obj

    async def get_client_links(self, email: str) -> list[str]:
        """GET /panel/api/clients/links/{email} — returns all proxy URLs."""
        try:
            data = await self._request("GET", f"clients/links/{email}")
            obj = data.get("obj") or []
            if isinstance(obj, list):
                return [str(link) for link in obj if link]
            return []
        except VPNAPIError as e:
            logger.warning(f"[{self.name}] get_client_links({email}) failed: {e}")
            return []

    async def extend_client(self, email: str, days: int) -> dict:
        """
        Extend client subscription via bulkAdjust (addDays).
        POST /panel/api/clients/bulkAdjust
        """
        body = {
            "emails": [email],
            "addDays": days,
        }
        return await self._request("POST", "clients/bulkAdjust", json=body)

    async def delete_client(self, email: str) -> dict:
        """POST /panel/api/clients/del/{email}"""
        try:
            return await self._request("POST", f"clients/del/{email}")
        except VPNAPIError as e:
            if e.status == 404:
                return {"success": True, "msg": "not found"}
            raise

    async def update_client(self, email: str, **kwargs) -> dict:
        """
        Update client fields via POST /panel/api/clients/update/{email}.
        kwargs: enable, expiryTime, total, etc.
        """
        body = {"email": email, **kwargs}
        return await self._request("POST", f"clients/update/{email}", json=body)

    async def reset_client_traffic(self, email: str) -> dict:
        """POST /panel/api/clients/resetTraffic/{email}"""
        return await self._request("POST", f"clients/resetTraffic/{email}")

    async def get_server_status(self) -> dict:
        """GET /panel/api/server/status"""
        data = await self._request("GET", "server/status")
        return data.get("obj") or {}

    async def get_online_clients(self) -> list[str]:
        """POST /panel/api/clients/onlines — returns list of online emails."""
        try:
            data = await self._request("POST", "clients/onlines")
            obj = data.get("obj") or []
            return list(obj) if isinstance(obj, list) else []
        except VPNAPIError as e:
            logger.warning(f"[{self.name}] get_online_clients failed: {e}")
            return []

    async def list_inbounds(self) -> list[dict]:
        """GET /panel/api/inbounds/list"""
        data = await self._request("GET", "inbounds/list")
        return data.get("obj") or []

    async def ping(self) -> bool:
        """Check connectivity by fetching server status."""
        try:
            await self.get_server_status()
            return True
        except VPNAPIError:
            return False


# ─── Multi-server facade ───────────────────────────────────────────────────────

class MultiServerVPN:
    """
    Aggregates operations across all configured 3X-UI servers.
    All public methods mirror the old single-server signatures so
    delivery.py and handlers need minimal changes.
    """

    def __init__(self, servers: list[dict]) -> None:
        self.clients: list[ThreeXUIClient] = [
            ThreeXUIClient(**s) for s in servers
        ]

    # ── Client lifecycle ─────────────────────────────────────────────────────

    async def create_client(
        self,
        email: str,
        days: int,
        limit_gb: int = 0,
        device_limit: int = 0,  # kept for API compat, ignored in 3X-UI
    ) -> dict:
        """
        Create client on ALL servers, in ALL configured inbounds.
        Returns merged info from the first successful server.
        """
        limit_bytes = limit_gb * 1_073_741_824 if limit_gb > 0 else 0
        first_result: dict = {}

        for client in self.clients:
            try:
                results = await client.add_client_to_all_inbounds(email, days, limit_bytes)
                ok_count = sum(1 for r in results if r["ok"])
                logger.info(
                    f"create_client {email} on {client.name}: "
                    f"{ok_count}/{len(results)} inbounds OK"
                )
                if ok_count > 0 and not first_result:
                    first_result = {"ok": True, "server": client.name}
            except Exception as e:
                logger.error(f"create_client {email} on {client.name}: {e}")

        if not first_result:
            raise VPNAPIError("Failed to create client on all servers")

        # Return unified payload for delivery.py
        return {
            "ok": True,
            "email": email,
            "name": email,
            "left_days": days,
            "traffic_limit_gb": limit_gb,
        }

    async def extend_client(self, email: str, days: int) -> dict:
        """Extend client on ALL servers."""
        any_ok = False
        for client in self.clients:
            try:
                await client.extend_client(email, days)
                logger.info(f"extend_client {email} on {client.name}: +{days}d OK")
                any_ok = True
            except VPNAPIError as e:
                logger.error(f"extend_client {email} on {client.name}: {e}")

        if not any_ok:
            raise VPNAPIError("Failed to extend client on all servers")

        return {"ok": True, "email": email, "name": email}

    async def get_client(self, email: str) -> dict:
        """
        Get client info from the first server that returns data.
        Also fetches traffic and merges it.
        """
        for client in self.clients:
            try:
                info = await client.get_client(email)
                if info:
                    # Enrich with traffic data
                    try:
                        traffic = await client.get_client_traffic(email)
                        if traffic:
                            info.setdefault("up", traffic.get("up", 0))
                            info.setdefault("down", traffic.get("down", 0))
                            info.setdefault("total", traffic.get("total", 0))
                            info.setdefault("expiryTime", traffic.get("expiryTime", 0))
                    except Exception:
                        pass
                    return info
            except VPNAPIError as e:
                logger.debug(f"get_client {email} on {client.name}: {e}")

        raise VPNAPIError(f"Client {email} not found on any server", 404)

    async def delete_client(self, email: str) -> dict:
        """Delete client from ALL servers."""
        for client in self.clients:
            try:
                await client.delete_client(email)
                logger.info(f"delete_client {email} on {client.name}: OK")
            except VPNAPIError as e:
                logger.error(f"delete_client {email} on {client.name}: {e}")
        return {"ok": True}

    async def get_all_links(self, email: str) -> list[str]:
        """
        Collect proxy URLs from ALL servers for the given email.
        This is the core of the aggregated subscription.
        """
        all_links: list[str] = []
        for client in self.clients:
            links = await client.get_client_links(email)
            all_links.extend(links)
            logger.debug(f"get_all_links {email} from {client.name}: {len(links)} links")
        return all_links

    async def get_server_statuses(self) -> list[dict]:
        """Get status from all servers."""
        statuses = []
        for client in self.clients:
            try:
                status = await client.get_server_status()
                statuses.append({"name": client.name, "ok": True, **status})
            except VPNAPIError as e:
                statuses.append({"name": client.name, "ok": False, "error": str(e)})
        return statuses

    async def reset_traffic(self, email: str) -> dict:
        """Reset traffic for client on ALL servers."""
        for client in self.clients:
            try:
                await client.reset_client_traffic(email)
                logger.info(f"reset_traffic {email} on {client.name}: OK")
            except VPNAPIError as e:
                logger.error(f"reset_traffic {email} on {client.name}: {e}")
        return {"ok": True}

    async def ban_client(self, email: str, reason: str = "") -> dict:
        """Disable client on ALL servers (3X-UI uses enable=false for ban)."""
        for client in self.clients:
            try:
                await client.update_client(email, enable=False, comment=f"banned: {reason}")
                logger.info(f"ban_client {email} on {client.name}: OK")
            except VPNAPIError as e:
                logger.error(f"ban_client {email} on {client.name}: {e}")
        return {"ok": True}

    async def unban_client(self, email: str) -> dict:
        """Re-enable client on ALL servers."""
        for client in self.clients:
            try:
                await client.update_client(email, enable=True, comment="")
                logger.info(f"unban_client {email} on {client.name}: OK")
            except VPNAPIError as e:
                logger.error(f"unban_client {email} on {client.name}: {e}")
        return {"ok": True}

    async def ping_all(self) -> dict[str, bool]:
        """Ping all servers, return {name: reachable}."""
        results = {}
        for client in self.clients:
            results[client.name] = await client.ping()
        return results


# ─── Module-level singleton ────────────────────────────────────────────────────

_multi_vpn = MultiServerVPN(_SERVERS)


# ─── Public API (backward-compat) ─────────────────────────────────────────────
# These functions mirror the old single-server signatures so that
# delivery.py, handlers/admin.py, api/routes/admin.py etc. need
# minimal changes.

async def create_client(
    name: str,
    days: int,
    limit_gb: int = 0,
    device_limit: int = 0,
) -> dict:
    return await _multi_vpn.create_client(name, days, limit_gb, device_limit)


async def extend_client(name: str, days: int) -> dict:
    return await _multi_vpn.extend_client(name, days)


async def get_client(name: str) -> dict:
    try:
        raw = await _multi_vpn.get_client(name)
        return normalize_client_payload(raw)
    except VPNAPIError as e:
        if e.status == 404:
            raise
        raise


async def delete_client(name: str) -> dict:
    return await _multi_vpn.delete_client(name)


async def ban_client(name: str, reason: str = "") -> dict:
    return await _multi_vpn.ban_client(name, reason)


async def unban_client(name: str) -> dict:
    return await _multi_vpn.unban_client(name)


async def get_all_links(name: str) -> list[str]:
    """Return ALL proxy URLs for a client across all servers."""
    return await _multi_vpn.get_all_links(name)


async def get_server_status() -> dict:
    """Return status from first available server (for compatibility)."""
    statuses = await _multi_vpn.get_server_statuses()
    if statuses:
        return statuses[0]
    return {}


async def get_all_server_statuses() -> list[dict]:
    """Return status from ALL servers."""
    return await _multi_vpn.get_server_statuses()


async def reset_traffic(name: str) -> dict:
    return await _multi_vpn.reset_traffic(name)


async def ping_servers() -> dict[str, bool]:
    return await _multi_vpn.ping_all()


async def list_clients() -> dict:
    """List clients from first server (compat)."""
    for client in _multi_vpn.clients:
        try:
            data = await client._request("GET", "clients/list")
            return data
        except VPNAPIError:
            continue
    return {"ok": False, "obj": []}


async def get_traffic_stats() -> dict:
    """Traffic stats from first server (compat)."""
    return await get_server_status()


async def get_logs(count: int = 50) -> dict:
    """Xray logs from first server."""
    for client in _multi_vpn.clients:
        try:
            data = await client._request("POST", f"server/xraylogs/{count}")
            return data
        except VPNAPIError:
            continue
    return {"ok": False, "obj": ""}


async def reissue_client_same_name(
    name: str,
    default_days: int = 30,
    default_limit_gb: int = 0,
    device_limit: int = 7,
) -> dict:
    """Delete and re-create client with the same name, preserving remaining days."""
    try:
        raw = await get_client(name)
        info = normalize_client_payload(raw)
        days = max(1, int(info.get("left_days") or default_days))
        limit_gb = int(info.get("traffic_limit_gb") or default_limit_gb)
    except VPNAPIError:
        days = default_days
        limit_gb = default_limit_gb

    await delete_client(name)
    created = await create_client(name, days, limit_gb, device_limit=device_limit)
    created["left_days"] = created.get("left_days") or days
    created["traffic_limit_gb"] = created.get("traffic_limit_gb") or limit_gb
    created["name"] = created.get("name") or name
    return created


def add_server(
    name: str,
    base_url: str,
    api_token: str,
    inbound_ids: list[int],
    ssl_verify: bool = False,
) -> None:
    """
    Dynamically add a server to the pool at runtime.
    Call this from __main__.py or config loading after env is ready.
    """
    global _multi_vpn
    new_client = ThreeXUIClient(
        name=name,
        base_url=base_url,
        api_token=api_token,
        inbound_ids=inbound_ids,
        ssl_verify=ssl_verify,
    )
    _multi_vpn.clients.append(new_client)
    logger.info(f"Server '{name}' added to VPN pool. Total: {len(_multi_vpn.clients)}")
