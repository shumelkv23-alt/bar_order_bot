from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.api import admin, miniapp, public, staff
from app.bot.handlers import router as bot_router
from app.config import get_settings
from app.db import async_session_factory, engine
from app.logging_config import configure_logging
from app.services.auto_progress import auto_progress_loop
from app.services.seed import seed_demo_data

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
web_root = Path(__file__).resolve().parent / "web"
templates = Jinja2Templates(directory=web_root / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.mini_app_url:
        if not settings.mini_app_url.startswith("https://"):
            raise RuntimeError("MINI_APP_BASE_URL must be public HTTPS")
        if not settings.bot_token:
            raise RuntimeError("BOT_TOKEN is required for Mini App")
        if (
            not settings.leaderboard_pseudonym_secret
            or len(settings.leaderboard_pseudonym_secret) < 32
        ):
            raise RuntimeError("LEADERBOARD_PSEUDONYM_SECRET must be at least 32 characters")
    if settings.seed_demo:
        async with async_session_factory() as session:
            await seed_demo_data(session)

    app.state.bot = None
    app.state.dispatcher = None
    if settings.bot_token:
        bot = Bot(
            settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(bot_router)
        app.state.bot = bot
        app.state.dispatcher = dispatcher
        if settings.webhook_url:
            await bot.set_webhook(
                settings.webhook_url,
                secret_token=settings.webhook_secret,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
    auto_task = None
    if settings.auto_progress_enabled:
        auto_task = asyncio.create_task(auto_progress_loop(async_session_factory, app.state.bot))
    try:
        yield
    finally:
        if auto_task:
            auto_task.cancel()
            with suppress(asyncio.CancelledError):
                await auto_task
    if app.state.bot:
        await app.state.bot.session.close()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="MVP API for Telegram drink orders at small bar events",
    lifespan=lifespan,
)
app.include_router(public.router)
app.include_router(staff.router)
app.include_router(admin.router)
app.include_router(miniapp.router)
app.mount("/static", StaticFiles(directory=web_root / "static"), name="static")
miniapp_root = web_root / "miniapp"
if not miniapp_root.exists():
    miniapp_root = web_root.parent.parent / "miniapp" / "dist"
if miniapp_root.exists():
    app.mount(
        "/miniapp/assets", StaticFiles(directory=miniapp_root / "assets"), name="miniapp-assets"
    )


@app.get("/miniapp", include_in_schema=False)
@app.get("/miniapp/", include_in_schema=False)
async def miniapp_page():
    index = miniapp_root / "index.html"
    if not index.exists():
        raise HTTPException(status_code=503, detail="Mini App frontend is not built")
    return FileResponse(index, headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def request_logging(request: Request, call_next):
    if settings.mini_app_url and (
        request.url.path == "/api/v1/users"
        or request.url.path.startswith("/api/v1/users/")
        or request.url.path == "/api/v1/orders"
        or request.url.path.startswith("/api/v1/orders/")
    ):
        return JSONResponse(
            {"detail": "Legacy guest API disabled while Mini App is public"},
            status_code=410,
            headers={"Cache-Control": "no-store"},
        )
    if request.url.path == "/api/v1/miniapp/secret-menu/quiz":
        maximum = 4096
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > maximum:
                    return JSONResponse({"detail": "Quiz request is too large"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid content length"}, status_code=400)
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > maximum:
                return JSONResponse({"detail": "Quiz request is too large"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    request_id = request.headers.get("X-Request-ID", "")[:100] or str(uuid4())
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        raise
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
        },
    )
    return response


@app.get("/health")
async def health() -> dict:
    async with async_session_factory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok", "environment": settings.environment}


@app.get("/", include_in_schema=False)
async def root(request: Request):
    return templates.TemplateResponse(request=request, name="home.html")


@app.get("/staff", include_in_schema=False)
async def staff_page(request: Request):
    return templates.TemplateResponse(request=request, name="staff.html")


@app.get("/admin", include_in_schema=False)
async def admin_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin.html")


@app.get("/owner", include_in_schema=False)
async def owner_page(request: Request):
    return templates.TemplateResponse(request=request, name="owner.html")


@app.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    bot: Bot | None = request.app.state.bot
    dispatcher: Dispatcher | None = request.app.state.dispatcher
    if not bot or not dispatcher:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot disabled")
    if x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid secret")
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dispatcher.feed_update(bot, update)
    return {"ok": True}
