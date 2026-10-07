from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://pixelshift:pixelshift@db:5432/pixelshift")
    jwt_secret: str = os.getenv("JWT_SECRET", "change-this-in-production")
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    max_image_mb: int = int(os.getenv("MAX_IMAGE_MB", "20"))
    max_media_mb: int = min(int(os.getenv("MAX_MEDIA_MB", "20")), 20)
    bot_token: str = os.getenv("BOT_TOKEN", "")
    telegram_admin_username: str = os.getenv("TELEGRAM_ADMIN_USERNAME", "MahdidBagheri").lstrip("@")
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    pro_price_cents: int = int(os.getenv("PRO_PRICE_CENTS", "30"))
    credit_pack_credits: int = int(os.getenv("CREDIT_PACK_CREDITS", "100"))
    credit_pack_price_cents: int = int(os.getenv("CREDIT_PACK_PRICE_CENTS", "100"))
    # Keep administrator credentials separate from customer accounts.  The
    # console is deliberately unavailable until both values are configured.
    admin_username: str = os.getenv("ADMIN_USERNAME", "")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    admin_session_secret: str = os.getenv("ADMIN_SESSION_SECRET", os.getenv("JWT_SECRET", "change-this-in-production"))


settings = Settings()
