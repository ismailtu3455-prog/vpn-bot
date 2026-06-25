import base64
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

app = FastAPI(title="Mock H1Cloud API")

VALID_TOKEN = "test_token"
DATA_FILE = Path(__file__).with_name("mock_vpn_data.json")

def check_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split(" ")[1]
    if token != VALID_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid token")

class CreateClientReq(BaseModel):
    name: str
    days: int
    traffic_limit_gb: int = 0
    device_limit: int = 0

class EditClientReq(BaseModel):
    name: str
    days: Optional[int] = None
    new_name: Optional[str] = None
    traffic_limit_gb: Optional[int] = None
    device_limit: Optional[int] = None

class BanClientReq(BaseModel):
    reason: str = ""


def load_clients() -> Dict[str, dict]:
    if not DATA_FILE.exists():
        return {}
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def save_clients() -> None:
    DATA_FILE.write_text(
        json.dumps(clients_db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# File-backed database for mock mode
clients_db: Dict[str, dict] = load_clients()


def build_mock_link(name: str) -> str:
    key_id = secrets.token_hex(16)
    return (
        f"vless://{key_id}@mock.adoria.fun:443"
        f"?type=tcp&security=reality&pbk=mockpbk&sni=cloudv.adoria.fun&fp=chrome#"
        f"{name}"
    )

@app.post("/api/create", dependencies=[])
async def create_client(req: CreateClientReq, request: Request):
    check_token(request.headers.get("authorization"))
    if req.name in clients_db:
        raise HTTPException(status_code=400, detail="Client already exists")
    
    expires_at = datetime.now(UTC) + timedelta(days=req.days)
    
    vless_url = build_mock_link(req.name)
    client_data = {
        "name": req.name,
        "subscription_url": f"http://127.0.0.1:25626/sub/{req.name}",
        "vless_url": vless_url,
        "links": [vless_url],
        "left_days": req.days,
        "traffic_limit_gb": req.traffic_limit_gb,
        "device_limit": req.device_limit,
        "used_traffic_bytes": 0,
        "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        "is_banned": False,
        "ban_reason": ""
    }
    clients_db[req.name] = client_data
    save_clients()
    return {"ok": True, "data": client_data}

@app.patch("/api/edit", dependencies=[])
async def edit_client(req: EditClientReq, request: Request):
    check_token(request.headers.get("authorization"))
    if req.name not in clients_db:
        raise HTTPException(status_code=404, detail="Client not found")
        
    client = clients_db[req.name]
    
    if req.days is not None:
        client["left_days"] += req.days
        current_exp = datetime.strptime(client["expires_at"], "%Y-%m-%d %H:%M:%S")
        client["expires_at"] = (current_exp + timedelta(days=req.days)).strftime("%Y-%m-%d %H:%M:%S")
        
    if req.traffic_limit_gb is not None:
        client["traffic_limit_gb"] = req.traffic_limit_gb
        
    if req.new_name is not None:
        clients_db[req.new_name] = client
        client["name"] = req.new_name
        client["subscription_url"] = f"http://127.0.0.1:25626/sub/{req.new_name}"
        if client.get("vless_url"):
            client["vless_url"] = build_mock_link(req.new_name)
        del clients_db[req.name]
    if client.get("vless_url"):
        client["links"] = [client["vless_url"]]
    save_clients()
    return {"ok": True, "data": client}

@app.get("/api/clients/{name}", dependencies=[])
async def get_client(name: str, request: Request):
    check_token(request.headers.get("authorization"))
    if name not in clients_db:
        raise HTTPException(status_code=404, detail="Client not found")
    client = clients_db[name]
    if client.get("vless_url") and not client.get("links"):
        client["links"] = [client["vless_url"]]
        save_clients()
    return {"ok": True, "data": client}

@app.delete("/api/clients/{name}", dependencies=[])
async def delete_client(name: str, request: Request):
    check_token(request.headers.get("authorization"))
    if name in clients_db:
        del clients_db[name]
        save_clients()
        return {"ok": True, "message": "Client deleted"}
    raise HTTPException(status_code=404, detail="Client not found")

@app.get("/api/clients", dependencies=[])
async def list_clients(request: Request):
    check_token(request.headers.get("authorization"))
    return {"ok": True, "data": list(clients_db.values())}

@app.patch("/api/clients/{name}/ban", dependencies=[])
async def ban_client(name: str, req: BanClientReq, request: Request):
    check_token(request.headers.get("authorization"))
    if name not in clients_db:
        raise HTTPException(status_code=404, detail="Client not found")
    clients_db[name]["is_banned"] = True
    clients_db[name]["ban_reason"] = req.reason
    save_clients()
    return {"ok": True, "message": "Banned"}

@app.patch("/api/clients/{name}/unban", dependencies=[])
async def unban_client(name: str, request: Request):
    check_token(request.headers.get("authorization"))
    if name not in clients_db:
        raise HTTPException(status_code=404, detail="Client not found")
    clients_db[name]["is_banned"] = False
    clients_db[name]["ban_reason"] = ""
    save_clients()
    return {"ok": True, "message": "Unbanned"}

@app.get("/api/status", dependencies=[])
async def server_status(request: Request):
    check_token(request.headers.get("authorization"))
    return {"ok": True, "data": {"status": "online", "uptime": "999 hours"}}


@app.get("/api/health", dependencies=[])
async def healthcheck(request: Request):
    check_token(request.headers.get("authorization"))
    return {"ok": True, "data": {"status": "healthy", "clients": len(clients_db)}}


@app.get("/api/traffic", dependencies=[])
async def traffic_stats(request: Request):
    check_token(request.headers.get("authorization"))
    total_used = sum(int(client.get("used_traffic_bytes", 0) or 0) for client in clients_db.values())
    return {"ok": True, "data": {"total_used_bytes": total_used, "clients": len(clients_db)}}


@app.get("/api/logs", dependencies=[])
async def logs(request: Request, count: int = 50):
    check_token(request.headers.get("authorization"))
    items = [
        {
            "event": "client_active",
            "name": client["name"],
            "expires_at": client.get("expires_at"),
        }
        for client in list(clients_db.values())[-count:]
    ]
    return {"ok": True, "data": items}


@app.get("/api/peers", dependencies=[])
async def peers(request: Request):
    check_token(request.headers.get("authorization"))
    return {"ok": True, "data": []}


@app.post("/api/peers", dependencies=[])
async def add_peer(request: Request):
    check_token(request.headers.get("authorization"))
    return {"ok": True, "message": "Mock peer accepted"}


@app.get("/api/node", dependencies=[])
async def node_name(request: Request):
    check_token(request.headers.get("authorization"))
    return {"ok": True, "data": {"name": "mock-h1cloud-node"}}


@app.get("/sub/{name}")
async def sub_file(name: str):
    client = clients_db.get(name)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    link = client.get("vless_url")
    if not link:
        link = build_mock_link(name)
        client["vless_url"] = link
        client["links"] = [link]
        save_clients()

    encoded = base64.b64encode(f"{link}\n".encode("utf-8")).decode("utf-8")
    return PlainTextResponse(encoded)

if __name__ == "__main__":
    print("Starting Mock VPN API on port 25626...")
    print("URL: http://127.0.0.1:25626/api")
    print("TOKEN: test_token")
    uvicorn.run(app, host="127.0.0.1", port=25626, log_level="info")
