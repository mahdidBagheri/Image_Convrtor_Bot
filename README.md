# PixelShift Telegram Image Converter

A Telegram bot that asks for an output format after every uploaded image, converts it with Pillow, and returns the converted file. A password-protected web dashboard shows users, conversion activity, and format statistics.

## Quick start

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Copy the environment template and set secure credentials:

   ```bash
   cp .env.example .env
   # Edit BOT_TOKEN, ADMIN_USERNAME, ADMIN_PASSWORD and SESSION_SECRET
   ```

3. Start the service:

   ```bash
   docker compose up --build -d
   ```

The admin panel is available at **http://37.27.84.251:5375/admin**. The health endpoint is `/health`.

## Supported output formats

JPEG, PNG, WEBP, GIF, BMP, TIFF and PDF are available from an inline keyboard. Telegram photos and image documents are accepted (up to `MAX_IMAGE_MB`, 20 MB by default). Pending uploads expire after 10 minutes.

## Local development

Requires Python 3.12+.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 5375 --reload
```

The bot uses long polling, so Telegram does not need inbound access to the server. SQLite data is kept at `DATA_DIR/converter.db`; Docker Compose persists `/app/data` in a named volume.

## Tests

```bash
pytest
```
