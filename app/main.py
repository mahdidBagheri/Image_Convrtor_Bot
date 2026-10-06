from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import jwt, stripe
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import UnidentifiedImageError
from .config import settings
from .converter import EXTENSIONS, FORMATS, convert_image
from .database import Conversion, Payment, Subscription, User, consume_allowance, get_session, initialize_database, refund_credit

password_hash = PasswordHash.recommended()
@asynccontextmanager
async def lifespan(_: FastAPI):
    await initialize_database(); yield
app = FastAPI(title="PixelShift API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
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
