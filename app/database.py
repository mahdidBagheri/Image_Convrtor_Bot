from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from .config import settings

class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    credit_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramUser(Base):
    """A Telegram identity, kept separate from password-based website accounts."""

    __tablename__ = "telegram_users"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    credit_balance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramProAccess(Base):
    __tablename__ = "telegram_pro_access"
    telegram_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class TelegramUserPreference(Base):
    __tablename__ = "telegram_user_preferences"
    telegram_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    language: Mapped[str] = mapped_column(String(5), default="fa", nullable=False)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(40), default="inactive")
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
class Conversion(Base):
    # This audit record intentionally contains metadata only.  Do not add image
    # bytes, filesystem paths, object-storage URLs, or thumbnails to this model.
    __tablename__ = "conversions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_format: Mapped[str] = mapped_column(String(20))
    destination_format: Mapped[str] = mapped_column(String(20))
    input_bytes: Mapped[int] = mapped_column(Integer)
    output_bytes: Mapped[int] = mapped_column(Integer)
    charge_type: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TelegramConversion(Base):
    """Metadata-only audit record for conversions made through the Telegram bot."""

    __tablename__ = "telegram_conversions"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"), index=True
    )
    source_format: Mapped[str] = mapped_column(String(20))
    destination_format: Mapped[str] = mapped_column(String(20))
    input_bytes: Mapped[int] = mapped_column(Integer)
    output_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class TelegramUsageEvent(Base):
    """A quota reservation linked to a successful conversion when it completes."""

    __tablename__ = "telegram_usage_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(20), index=True)
    plan: Mapped[str] = mapped_column(String(20))
    credit_charged: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    conversion_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_conversions.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default="pending")

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
async def initialize_database():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # create_all does not alter existing tables. These additive migrations
        # are safe to run concurrently at every API/bot startup.
        await connection.execute(text(
            "ALTER TABLE telegram_users ADD COLUMN IF NOT EXISTS "
            "credit_balance BIGINT NOT NULL DEFAULT 0"
        ))
        await connection.execute(text(
            "ALTER TABLE telegram_pro_access ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ"
        ))
        await connection.execute(text(
            "UPDATE telegram_pro_access SET expires_at = granted_at + INTERVAL '30 days' "
            "WHERE expires_at IS NULL"
        ))
        await connection.execute(text(
            "ALTER TABLE telegram_usage_events ADD COLUMN IF NOT EXISTS "
            "credit_charged BIGINT NOT NULL DEFAULT 0"
        ))
        # Preserve today's usage when this table is first introduced. Each
        # historical conversion is linked once, making startup idempotent.
        await connection.execute(text("""
            INSERT INTO telegram_usage_events
                (telegram_id, category, plan, conversion_id, created_at)
            SELECT
                conversion.telegram_id,
                CASE
                    WHEN conversion.source_format IN
                        ('JPEG', 'JPG', 'PNG', 'WEBP', 'GIF', 'BMP', 'TIFF',
                         'TIF', 'PDF', 'HEIC', 'HEIF', 'AVIF', 'JFIF')
                        THEN 'image'
                    WHEN conversion.source_format IN
                        ('TELEGRAM VOICE', 'MP3', 'WAV', 'M4A', 'AAC', 'FLAC',
                         'OGG', 'OPUS', 'OGA')
                        THEN 'voice'
                    ELSE 'video'
                END,
                'free',
                conversion.id,
                conversion.created_at
            FROM telegram_conversions AS conversion
            WHERE NOT EXISTS (
                SELECT 1 FROM telegram_usage_events AS usage
                WHERE usage.conversion_id = conversion.id
            )
            ON CONFLICT (conversion_id) DO NOTHING
        """))
async def get_session():
    async with SessionLocal() as session: yield session
async def consume_allowance(session: AsyncSession, user_id: int) -> str | None:
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    subscription = await session.scalar(select(Subscription).where(Subscription.user_id == user_id).with_for_update())
    now = datetime.now(timezone.utc)
    if subscription and subscription.status in {"active", "trialing"} and subscription.current_period_end and subscription.current_period_end > now: return "pro"
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used = await session.scalar(select(func.count(Conversion.id)).where(Conversion.user_id == user_id, Conversion.created_at >= today, Conversion.charge_type == "free")) or 0
    if used < 3: return "free"
    if user and user.credit_balance > 0:
        user.credit_balance -= 1
        return "credit"
    return None
async def refund_credit(session: AsyncSession, user_id: int, charge_type: str):
    if charge_type == "credit":
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user: user.credit_balance += 1


class Database:
    """Small persistence facade used by the Telegram dispatcher."""

    FREE_DAILY_LIMITS = {"image": 5, "video": 2, "voice": 3}
    CREDIT_PRICES = {"image": 2_000, "video": 10_000, "voice": 5_000}

    async def initialize(self) -> None:
        await initialize_database()

    async def touch_user(
        self, telegram_id: int, username: str | None, first_name: str | None
    ) -> None:
        async with SessionLocal() as session:
            user = await session.get(TelegramUser, telegram_id)
            if user is None:
                session.add(TelegramUser(
                    telegram_id=telegram_id,
                    username=username,
                    first_name=first_name,
                ))
            else:
                user.username = username
                user.first_name = first_name
                user.last_seen = datetime.now(timezone.utc)
            await session.commit()

    async def record_conversion(
        self,
        telegram_id: int,
        source: str,
        destination: str,
        input_bytes: int,
        output_bytes: int,
        usage_event_id: int | None = None,
    ) -> None:
        async with SessionLocal() as session:
            conversion = TelegramConversion(
                telegram_id=telegram_id,
                source_format=source,
                destination_format=destination,
                input_bytes=input_bytes,
                output_bytes=output_bytes,
            )
            session.add(conversion)
            await session.flush()
            if usage_event_id is not None:
                usage = await session.get(TelegramUsageEvent, usage_event_id)
                if usage is None or usage.telegram_id != telegram_id or usage.conversion_id is not None:
                    raise ValueError("Invalid Telegram usage reservation")
                usage.conversion_id = conversion.id
            await session.commit()

    async def reserve_conversion(
        self, telegram_id: int, category: str
    ) -> "TelegramReservation | None":
        """Atomically reserve free/Pro usage or debit Toman credit."""
        if category not in self.FREE_DAILY_LIMITS:
            raise ValueError(f"Unknown Telegram conversion category: {category}")
        async with SessionLocal() as session:
            user = await session.scalar(
                select(TelegramUser)
                .where(TelegramUser.telegram_id == telegram_id)
                .with_for_update()
            )
            if user is None:
                raise ValueError("Telegram user must be registered before reserving usage")

            now = datetime.now(timezone.utc)
            await session.execute(delete(TelegramUsageEvent).where(
                TelegramUsageEvent.telegram_id == telegram_id,
                TelegramUsageEvent.conversion_id.is_(None),
                TelegramUsageEvent.created_at < now - timedelta(minutes=15),
            ))
            access = await session.get(TelegramProAccess, telegram_id)
            is_pro = bool(access and access.expires_at and access.expires_at > now)
            plan = "pro" if is_pro else "free"
            credit_charged = 0
            if not is_pro:
                today = now.replace(hour=0, minute=0, second=0, microsecond=0)
                used = await session.scalar(select(func.count(TelegramUsageEvent.id)).where(
                    TelegramUsageEvent.telegram_id == telegram_id,
                    TelegramUsageEvent.category == category,
                    TelegramUsageEvent.created_at >= today,
                    TelegramUsageEvent.plan == "free",
                )) or 0
                if used >= self.FREE_DAILY_LIMITS[category]:
                    credit_charged = self.CREDIT_PRICES[category]
                    if user.credit_balance < credit_charged:
                        await session.commit()
                        return None
                    user.credit_balance -= credit_charged
                    plan = "credit"

            usage = TelegramUsageEvent(
                telegram_id=telegram_id,
                category=category,
                plan=plan,
                credit_charged=credit_charged,
            )
            session.add(usage)
            await session.flush()
            usage_id = usage.id
            await session.commit()
            return TelegramReservation(
                id=usage_id,
                source=plan,
                charged=credit_charged,
                balance=user.credit_balance,
            )

    async def release_reservation(self, usage_event_id: int) -> None:
        """Refund a failed conversion without deleting completed usage."""
        async with SessionLocal() as session:
            usage = await session.scalar(
                select(TelegramUsageEvent)
                .where(
                    TelegramUsageEvent.id == usage_event_id,
                    TelegramUsageEvent.conversion_id.is_(None),
                )
                .with_for_update()
            )
            if usage is not None:
                if usage.credit_charged:
                    user = await session.scalar(
                        select(TelegramUser)
                        .where(TelegramUser.telegram_id == usage.telegram_id)
                        .with_for_update()
                    )
                    if user is not None:
                        user.credit_balance += usage.credit_charged
                await session.delete(usage)
            await session.commit()

    async def get_quota_status(self, telegram_id: int) -> dict[str, int | None]:
        """Return remaining UTC-day quotas, or ``None`` values for Pro users."""
        async with SessionLocal() as session:
            access = await session.get(TelegramProAccess, telegram_id)
            now = datetime.now(timezone.utc)
            if access and access.expires_at and access.expires_at > now:
                return {category: None for category in self.FREE_DAILY_LIMITS}
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            rows = await session.execute(
                select(TelegramUsageEvent.category, func.count(TelegramUsageEvent.id))
                .where(
                    TelegramUsageEvent.telegram_id == telegram_id,
                    TelegramUsageEvent.created_at >= today,
                    TelegramUsageEvent.plan == "free",
                )
                .group_by(TelegramUsageEvent.category)
            )
            used = dict(rows.all())
            return {
                category: max(0, limit - int(used.get(category, 0)))
                for category, limit in self.FREE_DAILY_LIMITS.items()
            }

    async def get_telegram_id_by_username(self, username: str) -> int | None:
        """Resolve a known Telegram username without exposing IDs in configuration."""
        normalized = username.lstrip("@").lower()
        async with SessionLocal() as session:
            return await session.scalar(
                select(TelegramUser.telegram_id)
                .where(func.lower(TelegramUser.username) == normalized)
                .limit(1)
            )

    async def get_language(self, telegram_id: int) -> str:
        async with SessionLocal() as session:
            preference = await session.get(TelegramUserPreference, telegram_id)
            return preference.language if preference and preference.language in {"fa", "en"} else "fa"

    async def set_language(self, telegram_id: int, language: str) -> None:
        if language not in {"fa", "en"}:
            raise ValueError("Unsupported Telegram language")
        async with SessionLocal() as session:
            preference = await session.get(TelegramUserPreference, telegram_id)
            if preference is None:
                session.add(TelegramUserPreference(telegram_id=telegram_id, language=language))
            else:
                preference.language = language
            await session.commit()

    async def is_pro(self, telegram_id: int) -> bool:
        async with SessionLocal() as session:
            access = await session.get(TelegramProAccess, telegram_id)
            return bool(
                access and access.expires_at
                and access.expires_at > datetime.now(timezone.utc)
            )

    async def get_credit_balance(self, telegram_id: int) -> int:
        async with SessionLocal() as session:
            user = await session.get(TelegramUser, telegram_id)
            return user.credit_balance if user is not None else 0

    async def get_pro_expiry(self, telegram_id: int) -> datetime | None:
        async with SessionLocal() as session:
            access = await session.get(TelegramProAccess, telegram_id)
            if access and access.expires_at and access.expires_at > datetime.now(timezone.utc):
                return access.expires_at
            return None


@dataclass(frozen=True)
class TelegramReservation:
    id: int
    source: str
    charged: int
    balance: int
