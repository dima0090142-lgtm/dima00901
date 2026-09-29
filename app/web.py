import logging
import re
import time

from aiogram import Bot
from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web

from .common import application_kb, application_text
from .config import WEBAPP_DIR, Config
from .db import Database

log = logging.getLogger(__name__)

MAX_APPS_PER_HOUR = 3
INIT_DATA_TTL = 24 * 3600


def _clean(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def create_web_app(bot: Bot, db: Database, cfg: Config) -> web.Application:
    app = web.Application(client_max_size=64 * 1024)
    # Версия нужна, чтобы Telegram не показывал старые файлы из кэша после обновления
    version = str(int(time.time()))
    index_html = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8").replace("__V__", version)

    async def index(request: web.Request) -> web.Response:
        return web.Response(
            text=index_html, content_type="text/html", headers={"Cache-Control": "no-cache"}
        )

    async def photo_url(key: str) -> str:
        filename = await db.get_setting(key)
        return f"/uploads/{filename}" if filename else ""

    async def content(request: web.Request) -> web.Response:
        faq = await db.list_faq()
        promos = await db.list_promos()
        photos = await db.list_portfolio()
        return web.json_response({
            "master": cfg.master_name,
            "site": cfg.site_url,
            "about": await db.get_setting("about") or "",
            "address": await db.get_setting("address") or "",
            "contacts": await db.get_setting("contacts") or "",
            "faq": [{"q": f["question"], "a": f["answer"]} for f in faq],
            "promos": [p["text"] for p in promos],
            "portfolio": [f"/uploads/{p['filename']}" for p in photos],
            "hero_photo": await photo_url("hero_photo"),
            "master_photo": await photo_url("master_photo"),
        })

    async def apply(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Некорректный запрос"}, status=400)

        try:
            init = safe_parse_webapp_init_data(cfg.bot_token, str(data.get("initData", "")))
        except ValueError:
            return web.json_response({"error": "Откройте форму через Telegram"}, status=401)
        if init.user is None or time.time() - init.auth_date.timestamp() > INIT_DATA_TTL:
            return web.json_response({"error": "Сессия устарела, откройте приложение заново"}, status=401)

        name = _clean(data.get("name"), 100)
        phone = _clean(data.get("phone"), 40)
        idea = _clean(data.get("idea"), 1500)
        if not name:
            return web.json_response({"error": "Укажите имя"}, status=400)
        if len(re.sub(r"\D", "", phone)) < 10:
            return web.json_response({"error": "Проверьте номер телефона"}, status=400)

        user = init.user
        if await db.recent_applications_count(user.id, 3600) >= MAX_APPS_PER_HOUR:
            return web.json_response(
                {"error": "Вы уже отправили несколько заявок. Мы скоро свяжемся с вами!"}, status=429
            )

        await db.upsert_user(user.id, user.first_name, user.username)
        client = await db.upsert_client(phone, name, user.id)
        app_id = await db.add_application(user.id, name, phone, idea, client["id"] if client else None)
        application = await db.get_application(app_id)
        for admin_id in cfg.admin_ids:
            try:
                await bot.send_message(
                    admin_id, application_text(application, cfg.tz), reply_markup=application_kb(application)
                )
            except Exception:
                log.exception("Не удалось отправить заявку админу %s", admin_id)
        return web.json_response({"ok": True})

    app.router.add_get("/", index)
    app.router.add_get("/api/content", content)
    app.router.add_post("/api/apply", apply)
    app.router.add_static("/static", WEBAPP_DIR / "static")
    cfg.uploads_dir.mkdir(parents=True, exist_ok=True)
    app.router.add_static("/uploads", cfg.uploads_dir)
    return app
