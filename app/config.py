from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://pixelshift:pixelshift@db:5432/pixelshift")
    jwt_secret: str = os.getenv("JWT_SECRET", "change-this-in-production")
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    max_image_mb: int = int(os.getenv("MAX_IMAGE_MB", "20"))
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    pro_price_cents: int = int(os.getenv("PRO_PRICE_CENTS", "30"))
    credit_pack_credits: int = int(os.getenv("CREDIT_PACK_CREDITS", "100"))
    credit_pack_price_cents: int = int(os.getenv("CREDIT_PACK_PRICE_CENTS", "100"))


settings = Settings()
