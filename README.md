# Broker CRM

Telegram Mini App + backend for broker lead matching, Chatterfy sync and team analytics.

## Structure

- `web/` — Telegram Mini App UI
- `api/` — backend API
- `data/` — local development data (not committed)

## Roles

- Admin
- Founder
- SEO
- Head of Buying
- Handler

## MVP flow

Chatterfy -> API -> broker data -> REG/FTD/FT -> Chatterfy

The Mini App is the operator/admin interface.

## Next integrations

1. Chatterfy inbound webhook
2. FxPro CSV import
3. Chatterfy outbound sync
4. Telegram Mini App authentication
5. PostgreSQL
6. Role-based access
7. Analytics

## Environment (Render)

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL (Supabase) connection string |
| `TELEGRAM_BOT_TOKEN` | Enables signed Telegram login. **Set this in production** — without it anyone can call the API as any user |
| `TELEGRAM_NOTIFY_CHAT_ID` | Chat or group that gets new FTD notifications (add the bot to the group first) |
| `NOTIFY_EVENTS` | Which events notify, default `FTD` (e.g. `REG,FTD`) |
| `CHATTERFY_WEBHOOK_SECRET` | If set, Chatterfy must call `/webhook/chatterfy?secret=...` |
| `CHATTERFY_WEBHOOK_URL` | Outbound Chatterfy webhook |

## Leads workspace

The **Leads** tab (admin, head of buying, handler) lists every Chatterfy lead with its
funnel stage (LEAD → REG → FTD → FT), processing status, owner and notes.
Handlers see their own leads by default, can take free leads and update status;
admins and the head of buying can assign leads to anyone. Deposit amounts are hidden from handlers.
