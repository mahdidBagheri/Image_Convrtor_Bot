from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func, select
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
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
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
