"""FastAPI application and Telegram polling lifecycle."""

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path

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
bot = (
    Bot(settings.bot_token)
    if settings.bot_token and not settings.bot_token.startswith("replace-")
    else None
)
dispatcher = create_dispatcher(database, settings.max_image_mb)
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent


def _log_polling_failure(task: asyncio.Task[None]) -> None:
    """Surface unexpected polling failures instead of losing them silently."""
    if not task.cancelled() and task.exception() is not None:
        logger.error("Telegram polling stopped unexpectedly", exc_info=task.exception())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources and own the background polling task."""
    await database.initialize()
    polling: asyncio.Task[None] | None = None
    if bot:
        polling = asyncio.create_task(dispatcher.start_polling(bot))
        polling.add_done_callback(_log_polling_failure)
    try:
        yield
    finally:
        if polling:
            await dispatcher.stop_polling()
            polling.cancel()
            with suppress(asyncio.CancelledError):
                await polling
        if bot:
            await bot.session.close()


app = FastAPI(title="PixelShift Admin", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.session_https_only,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/health")
async def health() -> dict[str, str | bool]:
    return {"status": "ok", "bot_configured": bot is not None}


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=302)


@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/admin/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    valid = secrets.compare_digest(
        username, settings.admin_username
    ) and secrets.compare_digest(password, settings.admin_password)
    if not valid:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password."},
            status_code=401,
        )
    request.session["admin"] = True
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request) -> HTMLResponse | RedirectResponse:
    if not request.session.get("admin"):
        return RedirectResponse("/admin/login", status_code=303)
    stats = await database.dashboard()
    return templates.TemplateResponse(request, "dashboard.html", {"stats": stats})
