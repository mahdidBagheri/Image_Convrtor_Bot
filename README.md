# PixelShift

FastAPI, React and PostgreSQL image converter. Images are received, converted and returned in memory; the database stores only account, payment and conversion metadata—never image bytes, paths or URLs.

## Run

```bash
cp .env.example .env
docker compose up --build -d
```

The React app is at `http://localhost:5180`; API docs are at `http://localhost:8020/docs`.

## Admin console

Set `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `ADMIN_SESSION_SECRET` before starting the API, then visit `http://localhost:8020/admin`. The console includes overview metrics, searchable conversion metadata, user credit adjustments, billing records, and a privacy/retention page. It intentionally never displays images: images are processed in memory and the database contains only account, payment, and conversion metadata.

## Billing and access rules

- Each account receives three free conversions per UTC day.
- After that, one credit is charged per conversion (one credit = $0.01).
- Pro is $0.30/month and permits fair-use conversions without the daily allowance.
- Credits are offered as a 100-credit / $1 pack because common card processors do not accept one-cent card checkouts.
- Stripe grants credits/subscriptions only after its signed webhook arrives. Set its endpoint to `/api/billing/webhook` and subscribe it to `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, and `customer.subscription.deleted`.

For production, use a long random `JWT_SECRET`, HTTPS, a restricted `FRONTEND_URL`, and Stripe webhooks. The schema is created on startup; use Alembic migrations before evolving a production schema.
