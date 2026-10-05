from contextlib import asynccontextmanager
import secrets
from aiogram import Bot
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from .bot import create_dispatcher
from .config import settings
from .database import Database


database = Database(settings.database_path)
bot = Bot(settings.bot_token) if settings.bot_token and not settings.bot_token.startswith("replace-") else None
dispatcher = create_dispatcher(database, settings.max_image_mb)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.initialize()
    polling = None
    if bot:
        import asyncio
        polling = asyncio.create_task(dispatcher.start_polling(bot))
    yield
    if polling:
        await dispatcher.stop_polling()
        await polling
    if bot:
        await bot.session.close()


app = FastAPI(title="PixelShift Admin", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/health")
async def health():
    return {"status": "ok", "bot_configured": bot is not None}


@app.get("/")
async def root():
    return RedirectResponse("/admin", status_code=302)


@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/admin/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    valid = secrets.compare_digest(username, settings.admin_username) and secrets.compare_digest(password, settings.admin_password)
    if not valid:
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid username or password."}, status_code=401)
    request.session["admin"] = True
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    if not request.session.get("admin"):
        return RedirectResponse("/admin/login", status_code=303)
    stats = await database.dashboard()
    return templates.TemplateResponse(request, "dashboard.html", {"stats": stats})
