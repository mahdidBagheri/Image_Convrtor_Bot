from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import logging
import jwt, stripe
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash
from sqlalchemy import String, cast, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import UnidentifiedImageError
from starlette.middleware.sessions import SessionMiddleware
from fastapi.templating import Jinja2Templates
from .config import settings
from .converter import EXTENSIONS, FORMATS, convert_image
from .database import Conversion, Payment, Subscription, TelegramConversion, TelegramProAccess, TelegramUsageEvent, TelegramUser, TelegramUserPreference, User, consume_allowance, get_session, initialize_database, refund_credit

password_hash = PasswordHash.recommended()
@asynccontextmanager
async def lifespan(_: FastAPI):
    await initialize_database(); yield
app = FastAPI(title="PixelShift API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SessionMiddleware, secret_key=settings.admin_session_secret, https_only=False, same_site="lax")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


async def notify_telegram_user(telegram_id: int, language: str, fa: str, en: str) -> None:
    if not settings.bot_token:
        return
    try:
        async with Bot(settings.bot_token) as bot:
            await bot.send_message(telegram_id, fa if language == "fa" else en)
    except TelegramAPIError:
        logger.exception("Could not notify Telegram user %s", telegram_id)
class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
def token(user: User): return jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc)+timedelta(days=7)}, settings.jwt_secret, algorithm="HS256")
async def current_user(request: Request, session: AsyncSession = Depends(get_session)):
    header=request.headers.get("Authorization", "")
    if not header.startswith("Bearer "): raise HTTPException(401,"Sign in required")
    try: user_id=int(jwt.decode(header[7:],settings.jwt_secret,algorithms=["HS256"])["sub"])
    except (jwt.InvalidTokenError,ValueError,KeyError): raise HTTPException(401,"Invalid or expired session")
    user=await session.get(User,user_id)
    if not user: raise HTTPException(401,"Account not found")
    return user
@app.get("/health")
async def health(): return {"status":"ok"}

def admin_ready() -> bool:
    return bool(settings.admin_username and settings.admin_password)

def require_admin(request: Request):
    if not admin_ready():
        raise HTTPException(503, "Admin console is not configured")
    if request.session.get("admin") != settings.admin_username:
        return False
    return True

@app.get("/admin/login")
async def admin_login_page(request: Request):
    if not admin_ready():
        return templates.TemplateResponse(request, "login.html", {"error": "Set ADMIN_USERNAME and ADMIN_PASSWORD to enable the console."}, status_code=503)
    if require_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})

@app.post("/admin/login")
async def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if not admin_ready():
        raise HTTPException(503, "Admin console is not configured")
    # Constant-time comparison prevents a username/password timing oracle.
    import secrets
    if not (secrets.compare_digest(username, settings.admin_username) and secrets.compare_digest(password, settings.admin_password)):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid administrator credentials."}, status_code=401)
    request.session["admin"] = settings.admin_username
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)

async def admin_context(session: AsyncSession):
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    website_users = await session.scalar(select(func.count(User.id))) or 0
    telegram_users = await session.scalar(select(func.count(TelegramUser.telegram_id))) or 0
    website_conversions = await session.scalar(select(func.count(Conversion.id))) or 0
    telegram_conversions = await session.scalar(select(func.count(TelegramConversion.id))) or 0
    website_today = await session.scalar(select(func.count(Conversion.id)).where(Conversion.created_at >= today)) or 0
    telegram_today = await session.scalar(select(func.count(TelegramConversion.id)).where(TelegramConversion.created_at >= today)) or 0
    paid_revenue = await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "paid")) or 0
    telegram_label = func.coalesce(
        literal("@") + TelegramUser.username,
        TelegramUser.first_name,
        cast(TelegramUser.telegram_id, String),
    )
    activity = union_all(
        select(
            User.email.label("user"), Conversion.source_format, Conversion.destination_format,
            Conversion.output_bytes, Conversion.created_at, literal("Website").label("channel"),
        ).join(User, User.id == Conversion.user_id),
        select(
            telegram_label.label("user"), TelegramConversion.source_format,
            TelegramConversion.destination_format, TelegramConversion.output_bytes,
            TelegramConversion.created_at, literal("Telegram").label("channel"),
        ).join(TelegramUser, TelegramUser.telegram_id == TelegramConversion.telegram_id),
    ).subquery()
    recent = (await session.execute(select(activity).order_by(activity.c.created_at.desc()).limit(20))).mappings().all()
    format_rows = union_all(
        select(Conversion.destination_format.label("format")),
        select(TelegramConversion.destination_format.label("format")),
    ).subquery()
    formats = (await session.execute(
        select(format_rows.c.format, func.count().label("count"))
        .group_by(format_rows.c.format).order_by(func.count().desc()).limit(8)
    )).all()
    payments = (await session.execute(select(Payment, User.email).join(User, User.id == Payment.user_id).order_by(Payment.id.desc()).limit(100))).all()
    return {"overview": {"users": website_users + telegram_users, "website_users": website_users,
            "telegram_users": telegram_users, "conversions": website_conversions + telegram_conversions,
            "today": website_today + telegram_today, "revenue": paid_revenue,
            "recent": recent, "formats": formats}, "payments": payments}


async def statistics_context(session: AsyncSession):
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today - timedelta(days=6)
    trend_start = today - timedelta(days=13)

    total_users = await session.scalar(select(func.count(TelegramUser.telegram_id))) or 0
    active_users = await session.scalar(select(func.count(func.distinct(TelegramConversion.telegram_id)))) or 0
    daily_active = await session.scalar(select(func.count(func.distinct(TelegramConversion.telegram_id))).where(TelegramConversion.created_at >= today)) or 0
    weekly_active = await session.scalar(select(func.count(func.distinct(TelegramConversion.telegram_id))).where(TelegramConversion.created_at >= week_start)) or 0
    requests = await session.scalar(select(func.count(TelegramConversion.id))) or 0
    requests_today = await session.scalar(select(func.count(TelegramConversion.id)).where(TelegramConversion.created_at >= today)) or 0
    new_users_today = await session.scalar(select(func.count(TelegramUser.telegram_id)).where(TelegramUser.first_seen >= today)) or 0
    bytes_processed = await session.scalar(select(func.coalesce(func.sum(TelegramConversion.input_bytes), 0))) or 0

    request_rows = (await session.execute(
        select(func.date(TelegramConversion.created_at), func.count(TelegramConversion.id),
               func.count(func.distinct(TelegramConversion.telegram_id)))
        .where(TelegramConversion.created_at >= trend_start)
        .group_by(func.date(TelegramConversion.created_at))
    )).all()
    signup_rows = (await session.execute(
        select(func.date(TelegramUser.first_seen), func.count(TelegramUser.telegram_id))
        .where(TelegramUser.first_seen >= trend_start)
        .group_by(func.date(TelegramUser.first_seen))
    )).all()
    request_by_day = {row[0]: (row[1], row[2]) for row in request_rows}
    signup_by_day = {row[0]: row[1] for row in signup_rows}
    trend = []
    for offset in range(14):
        day = (trend_start + timedelta(days=offset)).date()
        day_requests, day_active = request_by_day.get(day, (0, 0))
        trend.append({"date": day, "new_users": signup_by_day.get(day, 0),
                      "active_users": day_active, "requests": day_requests})
    max_requests = max((day["requests"] for day in trend), default=0)
    max_new_users = max((day["new_users"] for day in trend), default=0)
    for day in trend:
        day["requests_height"] = (
            max(4, day["requests"] / max_requests * 100) if day["requests"] else 0
        )
        day["new_users_height"] = (
            max(4, day["new_users"] / max_new_users * 100) if day["new_users"] else 0
        )

    formats = (await session.execute(
        select(TelegramConversion.destination_format, func.count(TelegramConversion.id).label("count"))
        .group_by(TelegramConversion.destination_format)
        .order_by(func.count(TelegramConversion.id).desc())
    )).all()
    recent_users = (await session.execute(
        select(TelegramUser, func.count(TelegramConversion.id).label("requests"),
               func.max(TelegramConversion.created_at).label("last_request"))
        .outerjoin(TelegramConversion, TelegramConversion.telegram_id == TelegramUser.telegram_id)
        .group_by(TelegramUser.telegram_id)
        .order_by(TelegramUser.last_seen.desc()).limit(20)
    )).all()
    return {"statistics": {"total_users": total_users, "active_users": active_users,
            "daily_active": daily_active, "weekly_active": weekly_active,
            "requests": requests, "requests_today": requests_today,
            "new_users_today": new_users_today, "bytes_processed": bytes_processed,
            "active_rate": (active_users / total_users * 100) if total_users else 0,
            "requests_per_active": (requests / active_users) if active_users else 0,
            "trend": trend, "max_requests": max_requests,
            "max_new_users": max_new_users, "formats": formats,
            "recent_users": recent_users}}

async def conversion_context(session: AsyncSession, search: str = "", page: int = 1):
    """Return one page of metadata-only conversion audit records."""
    page = max(page, 1)
    limit = 50
    telegram_label = func.coalesce(
        literal("@") + TelegramUser.username,
        TelegramUser.first_name,
        cast(TelegramUser.telegram_id, String),
    )
    records = union_all(
        select(
            Conversion.id.label("record_id"), User.email.label("user"),
            Conversion.source_format, Conversion.destination_format,
            Conversion.input_bytes, Conversion.output_bytes,
            Conversion.charge_type.label("usage"), Conversion.created_at,
            literal("Website").label("channel"),
        ).join(User, User.id == Conversion.user_id),
        select(
            TelegramConversion.id.label("record_id"), telegram_label.label("user"),
            TelegramConversion.source_format, TelegramConversion.destination_format,
            TelegramConversion.input_bytes, TelegramConversion.output_bytes,
            func.coalesce(TelegramUsageEvent.plan, literal("bot")).label("usage"),
            TelegramConversion.created_at,
            literal("Telegram").label("channel"),
        ).join(TelegramUser, TelegramUser.telegram_id == TelegramConversion.telegram_id)
        .outerjoin(TelegramUsageEvent, TelegramUsageEvent.conversion_id == TelegramConversion.id),
    ).subquery()
    query = select(records)
    if search := search.strip():
        term = f"%{search}%"
        query = query.where(or_(
            records.c.user.ilike(term),
            records.c.source_format.ilike(term),
            records.c.destination_format.ilike(term),
            records.c.usage.ilike(term),
            records.c.channel.ilike(term),
        ))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (await session.execute(
        query.order_by(records.c.created_at.desc(), records.c.record_id.desc())
        .limit(limit)
        .offset((page - 1) * limit)
    )).mappings().all()
    return {
        "conversions": rows,
        "conversion_total": total,
        "search": search,
        "page": page,
        "has_previous": page > 1,
        "has_next": page * limit < total,
    }

async def users_context(session: AsyncSession, search: str = ""):
    now = datetime.now(timezone.utc)
    query = select(User).order_by(User.created_at.desc())
    telegram_query = (
        select(TelegramUser, func.count(TelegramConversion.id).label("requests"),
               func.max(TelegramConversion.created_at).label("last_request"),
               TelegramProAccess.expires_at.label("pro_expires_at"),
               (TelegramProAccess.expires_at > now).label("is_pro"))
        .outerjoin(TelegramConversion, TelegramConversion.telegram_id == TelegramUser.telegram_id)
        .outerjoin(TelegramProAccess, TelegramProAccess.telegram_id == TelegramUser.telegram_id)
        .group_by(TelegramUser.telegram_id, TelegramProAccess.telegram_id, TelegramProAccess.expires_at)
        .order_by(TelegramUser.last_seen.desc())
    )
    if search := search.strip():
        query = query.where(User.email.ilike(f"%{search}%"))
        telegram_query = telegram_query.where(or_(
            TelegramUser.username.ilike(f"%{search}%"),
            TelegramUser.first_name.ilike(f"%{search}%"),
            cast(TelegramUser.telegram_id, String).ilike(f"%{search}%"),
        ))
    return {"users": (await session.execute(query.limit(50))).scalars().all(),
            "telegram_users": (await session.execute(telegram_query.limit(100))).all(),
            "search": search}

@app.get("/admin")
@app.get("/admin/{tab}")
async def admin_console(request: Request, tab: str = "overview", q: str = "", page: int = 1, session: AsyncSession = Depends(get_session)):
    if not require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    if tab not in {"overview", "statistics", "conversions", "users", "billing", "privacy"}:
        raise HTTPException(404, "Unknown admin section")
    context = await admin_context(session)
    if tab == "statistics":
        context.update(await statistics_context(session))
    elif tab == "conversions":
        context.update(await conversion_context(session, q, page))
    elif tab == "users":
        context.update(await users_context(session, q))
    context.update({"request": request, "tab": tab, "admin_username": settings.admin_username})
    return templates.TemplateResponse(request, "dashboard.html", context)

@app.post("/admin/users/{user_id}/credits")
async def admin_adjust_credits(request: Request, user_id: int, adjustment: int = Form(...), session: AsyncSession = Depends(get_session)):
    if not require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user.credit_balance = max(0, user.credit_balance + adjustment)
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/telegram-users/{telegram_id}/pro")
async def admin_toggle_telegram_pro(request: Request, telegram_id: int, action: str = Form("grant"), session: AsyncSession = Depends(get_session)):
    if not require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    user = await session.get(TelegramUser, telegram_id)
    if not user:
        raise HTTPException(404, "Telegram user not found")
    preference = await session.get(TelegramUserPreference, telegram_id)
    language = preference.language if preference and preference.language in {"fa", "en"} else "fa"
    access = await session.get(TelegramProAccess, telegram_id)
    now = datetime.now(timezone.utc)
    if action == "remove":
        if access:
            await session.delete(access)
        notification = (
            "دسترسی پرو حساب شما غیرفعال شد.",
            "Your Pro access has been deactivated.",
        )
    elif action == "grant":
        base = max(now, access.expires_at) if access and access.expires_at else now
        expires_at = base + timedelta(days=30)
        if access:
            access.granted_at = now
            access.expires_at = expires_at
        else:
            session.add(TelegramProAccess(
                telegram_id=telegram_id,
                granted_at=now,
                expires_at=expires_at,
            ))
        notification = (
            f"⭐ حساب شما به پرو ارتقا یافت. دسترسی پرو تا {expires_at:%Y-%m-%d %H:%M} UTC فعال است.",
            f"⭐ Your account is now Pro. Pro access is active until {expires_at:%Y-%m-%d %H:%M} UTC.",
        )
    else:
        raise HTTPException(400, "Unknown Pro action")
    await session.commit()
    await notify_telegram_user(telegram_id, language, *notification)
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/telegram-users/{telegram_id}/credits")
async def admin_adjust_telegram_credits(
    request: Request,
    telegram_id: int,
    adjustment: int = Form(...),
    session: AsyncSession = Depends(get_session),
):
    if not require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    user = await session.scalar(
        select(TelegramUser)
        .where(TelegramUser.telegram_id == telegram_id)
        .with_for_update()
    )
    if not user:
        raise HTTPException(404, "Telegram user not found")
    preference = await session.get(TelegramUserPreference, telegram_id)
    language = preference.language if preference and preference.language in {"fa", "en"} else "fa"
    previous = user.credit_balance
    user.credit_balance = max(0, user.credit_balance + adjustment)
    actual_adjustment = user.credit_balance - previous
    balance = user.credit_balance
    await session.commit()
    if actual_adjustment:
        await notify_telegram_user(
            telegram_id,
            language,
            f"💰 اعتبار حساب شما {actual_adjustment:+,} تومان تغییر کرد. موجودی جدید: {balance:,} تومان.",
            f"💰 Your credit changed by {actual_adjustment:+,} T. New balance: {balance:,} T.",
        )
    return RedirectResponse("/admin/users", status_code=303)
@app.post("/api/auth/register")
async def register(data:Credentials,session:AsyncSession=Depends(get_session)):
    email=data.email.lower()
    if await session.scalar(select(User.id).where(User.email==email)): raise HTTPException(409,"An account with that email already exists")
    user=User(email=email,password_hash=password_hash.hash(data.password));session.add(user);await session.commit();await session.refresh(user)
    return {"token":token(user)}
@app.post("/api/auth/login")
async def login(data:Credentials,session:AsyncSession=Depends(get_session)):
    user=await session.scalar(select(User).where(User.email==data.email.lower()))
    if not user or not password_hash.verify(data.password,user.password_hash): raise HTTPException(401,"Incorrect email or password")
    return {"token":token(user)}
@app.get("/api/me")
async def me(user:User=Depends(current_user),session:AsyncSession=Depends(get_session)):
    subscription=await session.scalar(select(Subscription).where(Subscription.user_id==user.id));today=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    free_used=await session.scalar(select(func.count(Conversion.id)).where(Conversion.user_id==user.id,Conversion.created_at>=today,Conversion.charge_type=="free")) or 0
    now=datetime.now(timezone.utc);pro=bool(subscription and subscription.status in {"active","trialing"} and subscription.current_period_end and subscription.current_period_end>now)
    return {"email":user.email,"creditBalance":user.credit_balance,"freeRemaining":max(0,3-free_used),"pro":pro,"subscriptionEndsAt":subscription.current_period_end if pro else None}
@app.post("/api/convert")
async def convert(destination:str,image:UploadFile=File(...),user:User=Depends(current_user),session:AsyncSession=Depends(get_session)):
    destination=destination.lower()
    if destination not in FORMATS: raise HTTPException(400,"Unsupported destination format")
    data=await image.read()
    if not data or len(data)>settings.max_image_mb*1024*1024: raise HTTPException(413,f"Images must be between 1 byte and {settings.max_image_mb} MB")
    charge_type=await consume_allowance(session,user.id)
    if not charge_type: raise HTTPException(402,"Daily free conversions are used. Add credits or upgrade to Pro.")
    try: result,source=convert_image(data,destination)
    except (UnidentifiedImageError,OSError,ValueError,KeyError):
        await refund_credit(session,user.id,charge_type);await session.commit();raise HTTPException(400,"This file is not a readable image")
    output_format=FORMATS[destination];session.add(Conversion(user_id=user.id,source_format=source,destination_format=output_format,input_bytes=len(data),output_bytes=len(result),charge_type=charge_type));await session.commit()
    return StreamingResponse(BytesIO(result),media_type=f"image/{destination}",headers={"Content-Disposition":f'attachment; filename="converted.{EXTENSIONS[output_format]}"',"X-Usage-Source":charge_type})
def stripe_ready():
    if not settings.stripe_secret_key: raise HTTPException(503,"Payments are not configured yet")
    stripe.api_key=settings.stripe_secret_key
@app.post("/api/billing/checkout/{kind}")
async def checkout(kind:str,user:User=Depends(current_user),session:AsyncSession=Depends(get_session)):
    if kind not in {"pro","credits"}: raise HTTPException(404,"Unknown product")
    stripe_ready();metadata={"user_id":str(user.id),"kind":kind}
    if kind=="pro":
        cs=stripe.checkout.Session.create(mode="subscription",customer=user.stripe_customer_id or None,client_reference_id=str(user.id),metadata=metadata,subscription_data={"metadata":metadata},line_items=[{"price_data":{"currency":"usd","product_data":{"name":"PixelShift Pro"},"unit_amount":settings.pro_price_cents,"recurring":{"interval":"month"}},"quantity":1}],success_url=f"{settings.frontend_url}/?payment=success",cancel_url=f"{settings.frontend_url}/?payment=cancelled");amount=Decimal(settings.pro_price_cents)/100
    else:
        cs=stripe.checkout.Session.create(mode="payment",customer=user.stripe_customer_id or None,client_reference_id=str(user.id),metadata=metadata,line_items=[{"price_data":{"currency":"usd","product_data":{"name":f"{settings.credit_pack_credits} PixelShift credits"},"unit_amount":settings.credit_pack_price_cents},"quantity":1}],success_url=f"{settings.frontend_url}/?payment=success",cancel_url=f"{settings.frontend_url}/?payment=cancelled");amount=Decimal(settings.credit_pack_price_cents)/100
    session.add(Payment(user_id=user.id,stripe_checkout_session_id=cs.id,kind=kind,amount=amount));await session.commit();return {"checkoutUrl":cs.url}
@app.post("/api/billing/webhook")
async def webhook(request:Request,session:AsyncSession=Depends(get_session)):
    stripe_ready()
    try: event=stripe.Webhook.construct_event(await request.body(),request.headers.get("stripe-signature",""),settings.stripe_webhook_secret)
    except (ValueError,stripe.error.SignatureVerificationError): raise HTTPException(400,"Invalid webhook")
    obj=event["data"]["object"]
    if event["type"]=="checkout.session.completed":
        payment=await session.scalar(select(Payment).where(Payment.stripe_checkout_session_id==obj["id"]).with_for_update())
        if payment and payment.status!="paid":
            payment.status="paid";user=await session.get(User,payment.user_id);user.stripe_customer_id=obj.get("customer") or user.stripe_customer_id
            if payment.kind=="credits": user.credit_balance+=settings.credit_pack_credits
    elif event["type"] in {"customer.subscription.created","customer.subscription.updated","customer.subscription.deleted"}:
        uid=obj.get("metadata",{}).get("user_id")
        if uid:
            subscription=await session.scalar(select(Subscription).where(Subscription.user_id==int(uid)).with_for_update()) or Subscription(user_id=int(uid));session.add(subscription)
            subscription.stripe_subscription_id=obj["id"];subscription.status=obj["status"];subscription.current_period_end=datetime.fromtimestamp(obj["current_period_end"],tz=timezone.utc)
    await session.commit();return {"received":True}
