# PixelShift

FastAPI, React, Telegram and PostgreSQL image converter. Images are received, converted and returned in memory; the database stores only account, payment and conversion metadata—never image bytes, paths or URLs.

## Run

```bash
cp .env.example .env
docker compose up --build -d
```

The React app is at `http://localhost:5180`; API docs are at `http://localhost:8020/docs`.

## Telegram bot

Set `BOT_TOKEN` in `.env` to the token supplied by BotFather. The `bot` Compose service starts long polling automatically. A user can send or forward a photo, image document, or album; the bot then asks for JPEG, PNG, WEBP, GIF, BMP, TIFF, or PDF and returns each converted file as a document.

The bot also accepts videos, audio files, and Telegram-recorded voice notes up to 20 MB. Videos can become a normal MP4, silent MP4, extracted MP3, or an OGG/Opus Telegram voice message. Audio and voice messages can become MP3, MP4, or Telegram voice. MP4 output is capped at 1080p. Free Telegram users can convert media under one minute and receive 5 image, 2 video, and 3 audio/voice conversions per UTC day. After those quotas, images cost 2,000 T, videos 10,000 T, and audio/voice conversions 5,000 T from the user's balance. A 30-day Pro grant costs 100,000 T, accepts media under five minutes, and has no daily quota. `/credit`, `/add_credit`, and `/upgrade` provide the user-facing balance and receipt workflows; Persian is the default language and `/language` switches to English.

## Admin console

Set `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `ADMIN_SESSION_SECRET` before starting the API, then visit `http://localhost:8020/admin`. The console includes synchronized website and Telegram users, bot activity statistics, searchable cross-channel conversion metadata, Telegram Toman balance adjustments, 30-day Pro grants, billing records, and a privacy/retention page. Granting Pro or changing Telegram credit sends the user a bot notification. It intentionally never displays images: images are processed in memory and the database contains only identity, payment and conversion metadata.

## Billing and access rules

- Each account receives three free conversions per UTC day.
- After that, one credit is charged per conversion (one credit = $0.01).
- Pro is $0.30/month and permits fair-use conversions without the daily allowance.
- Credits are offered as a 100-credit / $1 pack because common card processors do not accept one-cent card checkouts.
- Stripe grants credits/subscriptions only after its signed webhook arrives. Set its endpoint to `/api/billing/webhook` and subscribe it to `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, and `customer.subscription.deleted`.

For production, use a long random `JWT_SECRET`, HTTPS, a restricted `FRONTEND_URL`, and Stripe webhooks. The schema is created on startup; use Alembic migrations before evolving a production schema.
