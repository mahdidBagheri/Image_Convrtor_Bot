from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "change-me")
    session_secret: str = os.getenv("SESSION_SECRET", "development-only-secret")
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))
    max_image_mb: int = int(os.getenv("MAX_IMAGE_MB", "20"))

    @property
    def database_path(self) -> Path:
        return self.data_dir / "converter.db"


settings = Settings()
