from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from bot.config import settings

from bot.api.routes import user, admin, webhooks, payments, public

app = FastAPI(title="Adoria VPN API", version="2.0.0")

allowed_origins = {
    settings.site_origin,
    "https://cloud.adoria.fun",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(user.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(webhooks.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(public.router)

async def start_api_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.api_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
