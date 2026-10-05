"""Environment-backed application configuration."""

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "change-me")
    session_secret: str = os.getenv("SESSION_SECRET", "development-only-secret")
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))
    max_image_mb: int = _positive_int("MAX_IMAGE_MB", 20)
    session_https_only: bool = os.getenv("SESSION_HTTPS_ONLY", "false").casefold() in {
        "1", "true", "yes"
    }

    @property
    def database_path(self) -> Path:
        return self.data_dir / "converter.db"


settings = Settings()
