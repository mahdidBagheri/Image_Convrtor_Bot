from pathlib import Path
import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
  first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS conversions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
  source_format TEXT NOT NULL, destination_format TEXT NOT NULL,
  input_bytes INTEGER NOT NULL, output_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def touch_user(self, telegram_id: int, username: str | None, first_name: str | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username,
                first_name=excluded.first_name, last_seen=CURRENT_TIMESTAMP""",
                (telegram_id, username, first_name),
            )
            await db.commit()

    async def record_conversion(self, telegram_id: int, source: str, destination: str,
                                input_bytes: int, output_bytes: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO conversions (telegram_id, source_format, destination_format, input_bytes, output_bytes) VALUES (?, ?, ?, ?, ?)",
                (telegram_id, source, destination, input_bytes, output_bytes),
            )
            await db.commit()

    async def dashboard(self) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            users = (await (await db.execute("SELECT COUNT(*) n FROM users")).fetchone())["n"]
            conversions = (await (await db.execute("SELECT COUNT(*) n FROM conversions")).fetchone())["n"]
            today = (await (await db.execute("SELECT COUNT(*) n FROM conversions WHERE date(created_at)=date('now')")).fetchone())["n"]
            formats = await (await db.execute("SELECT destination_format format, COUNT(*) count FROM conversions GROUP BY destination_format ORDER BY count DESC")).fetchall()
            recent = await (await db.execute("""SELECT c.*, u.username, u.first_name FROM conversions c
                LEFT JOIN users u ON u.telegram_id=c.telegram_id ORDER BY c.id DESC LIMIT 20""")).fetchall()
            return {"users": users, "conversions": conversions, "today": today,
                    "formats": [dict(row) for row in formats], "recent": [dict(row) for row in recent]}
