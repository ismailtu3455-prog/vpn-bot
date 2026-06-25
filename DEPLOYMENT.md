# Two-Host Deployment

This project is designed for exactly two hosts:

1. `cloudv.adoria.fun` on Cloudflare Pages
2. one Python 3.12 backend host on H1Cloud

## Host Responsibilities

### Cloudflare Pages

Serves only the frontend and proxies these paths to the backend host:

- `/api/*`
- `/sub/*`
- `/info/*`
- `/health`

Set `API_ORIGIN` in `web/wrangler.jsonc` to your H1Cloud backend origin.
Static files are built into `web/public` via `npm run build`.

Example:

```json
{
  "vars": {
    "API_ORIGIN": "https://backend.example.com"
  }
}
```

### H1Cloud Python Host

Runs one shared backend application:

- FastAPI API
- Telegram bot polling worker
- payment webhooks
- subscription endpoints

Start with:

```bash
python run.py
```

## Required Environment

Backend `.env` example:

```env
BOT_TOKEN=123456:token
ADMIN_IDS=123456789
DB_PATH=bot.db

VPN_API_URL=https://vpn.example.com/api
VPN_API_TOKEN=secret

API_PORT=8888
API_SECRET=change-this-secret

SITE_DOMAIN=cloudv.adoria.fun
SUBSCRIPTION_DOMAIN=cloudv.adoria.fun
BOT_USERNAME=Adoria_funbot
```

## Routing Model

- browser requests `https://cloudv.adoria.fun/api/...`
- Cloudflare Pages Function proxies to `API_ORIGIN/api/...`
- subscription clients request `https://cloudv.adoria.fun/sub/<token>`
- Cloudflare Pages Function proxies to `API_ORIGIN/sub/<token>`

The Telegram bot and the website share the same database and business logic through the backend host.
