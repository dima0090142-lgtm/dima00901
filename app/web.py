import base64
import binascii
import html
import logging
import re
import time
import uuid
from datetime import datetime

from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.utils.web_app import WebAppInitData, safe_parse_webapp_init_data
from aiohttp import web

from .common import MONTHS, WEEKDAYS, application_kb, application_text, fmt_dt
from .config import WEBAPP_DIR, Config
from .db import Database
from .payments import rub

log = logging.getLogger(__name__)

MAX_APPS_PER_HOUR = 3
INIT_DATA_TTL = 24 * 3600
MAX_PHOTO_BYTES = 6 * 1024 * 1024

# Варианты быстрых ответов в форме — принимаем только их
DETAIL_OPTIONS = {
    "size": ("Размер", {"До 5 см", "5–15 см", "Больше 15 см", "Рукав / спина", "Не знаю"}),
    "place": ("Место", {"Рука", "Нога", "Спина", "Грудь", "Шея", "Другое"}),
    "color": ("Цвет", {"Чёрно-белая", "Цветная", "Не знаю"}),
}
IMAGE_SIGNATURES = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"RIFF": "webp"}


def _clean(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _details(raw) -> str:
    raw = raw if isinstance(raw, dict) else {}
    parts = []
    for key, (label, allowed) in DETAIL_OPTIONS.items():
        value = raw.get(key)
        if value in allowed:
            parts.append(f"{label}: {value}")
    return " · ".join(parts)


def _decode_photo(data_url) -> tuple[bytes, str] | None:
    """Разбирает фото из формы (data:image/...;base64,...). Возвращает байты и расширение."""
    if not isinstance(data_url, str) or not data_url.startswith("data:image/") or "," not in data_url:
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) > MAX_PHOTO_BYTES:
        return None
    for signature, ext in IMAGE_SIGNATURES.items():
        if raw.startswith(signature):
            return raw, ext
    return None


def _plain(text: str) -> str:
    """Telegram-HTML (жирный и т. п.) → обычный текст для мини-приложения."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _username(value: str) -> str:
    """«@name», «t.me/name» или «https://t.me/name» → «name»."""
    value = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)?@?", "", (value or "").strip())
    return value if re.fullmatch(r"[A-Za-z0-9_]{4,32}", value) else ""


def create_web_app(bot: Bot, db: Database, cfg: Config) -> web.Application:
    app = web.Application(client_max_size=8 * 1024 * 1024)
    # Версия нужна, чтобы Telegram не показывал старые файлы из кэша после обновления
    version = str(int(time.time()))
    index_html = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8").replace("__V__", version)
    # Референсы клиентов — личные фото, поэтому храним их вне публичной папки /uploads
    refs_dir = cfg.data_dir / "refs"

    def auth(data: dict) -> WebAppInitData | None:
        """Проверяет подпись Telegram: запрос действительно пришёл из мини-приложения этого бота."""
        try:
            init = safe_parse_webapp_init_data(cfg.bot_token, str(data.get("initData", "")))
        except ValueError:
            return None
        if init.user is None or time.time() - init.auth_date.timestamp() > INIT_DATA_TTL:
            return None
        return init

    async def read_json(request: web.Request) -> dict | None:
        try:
            data = await request.json()
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    async def index(request: web.Request) -> web.Response:
        return web.Response(
            text=index_html, content_type="text/html", headers={"Cache-Control": "no-cache"}
        )

    async def photo_url(key: str) -> str:
        filename = await db.get_setting(key)
        return f"/uploads/{filename}" if filename else ""

    async def bot_username() -> str | None:
        try:
            return (await bot.me()).username  # aiogram запоминает ответ после первого запроса
        except Exception:
            return None

    async def content(request: web.Request) -> web.Response:
        faq = await db.list_faq()
        promos = await db.list_promos()
        photos = await db.list_portfolio()
        return web.json_response({
            "master": cfg.master_name,
            "site": cfg.site_url,
            "bot": await bot_username(),
            "about": await db.get_setting("about") or "",
            "address": await db.get_setting("address") or "",
            "map_url": await db.get_setting("map_url") or "",
            "master_chat": _username(await db.get_setting("master_chat") or ""),
            "contacts": await db.get_setting("contacts") or "",
            "faq": [{"q": f["question"], "a": f["answer"]} for f in faq],
            "promos": [p["text"] for p in promos],
            "portfolio": [f"/uploads/{p['filename']}" for p in photos],
            "hero_photo": await photo_url("hero_photo"),
            "master_photo": await photo_url("master_photo"),
        })

    async def apply(request: web.Request) -> web.Response:
        data = await read_json(request)
        if data is None:
            return web.json_response({"error": "Некорректный запрос"}, status=400)
        init = auth(data)
        if init is None:
            return web.json_response({"error": "Откройте форму через Telegram"}, status=401)

        name = _clean(data.get("name"), 100)
        phone = _clean(data.get("phone"), 40)
        idea = _clean(data.get("idea"), 1500)
        if not name:
            return web.json_response({"error": "Укажите имя"}, status=400)
        if len(re.sub(r"\D", "", phone)) < 10:
            return web.json_response({"error": "Проверьте номер телефона"}, status=400)
        photo = None
        if data.get("photo"):
            photo = _decode_photo(data["photo"])
            if photo is None:
                return web.json_response({"error": "Не получилось прочитать фото — попробуйте другое"}, status=400)

        user = init.user
        if await db.recent_applications_count(user.id, 3600) >= MAX_APPS_PER_HOUR:
            return web.json_response(
                {"error": "Вы уже отправили несколько заявок. Мы скоро свяжемся с вами!"}, status=429
            )

        ref_file = None
        if photo:
            refs_dir.mkdir(parents=True, exist_ok=True)
            ref_file = f"{uuid.uuid4().hex}.{photo[1]}"
            (refs_dir / ref_file).write_bytes(photo[0])

        await db.upsert_user(user.id, user.first_name, user.username)
        client = await db.upsert_client(phone, name, user.id)
        app_id = await db.add_application(
            user.id, name, phone, idea, client["id"] if client else None,
            details=_details(data.get("details")), ref_photo=ref_file,
        )
        application = await db.get_application(app_id)
        for admin_id in cfg.admin_ids:
            try:
                await bot.send_message(
                    admin_id, application_text(application, cfg.tz), reply_markup=application_kb(application)
                )
                if ref_file:
                    await bot.send_photo(
                        admin_id, FSInputFile(refs_dir / ref_file), caption=f"📎 Референс к заявке №{app_id}"
                    )
            except Exception:
                log.exception("Не удалось отправить заявку админу %s", admin_id)
        return web.json_response({"ok": True})

    async def me(request: web.Request) -> web.Response:
        """«Мои записи»: сеансы, заявки и предоплаты самого клиента."""
        data = await read_json(request)
        init = auth(data) if data is not None else None
        if init is None:
            return web.json_response({"error": "Откройте приложение через Telegram"}, status=401)
        user_id = init.user.id
        client = await db.client_by_user(user_id)
        now = int(time.time())

        upcoming, past = [], []
        for a in await db.user_appointments(user_id, client["id"] if client else None):
            dt = datetime.fromtimestamp(a["starts_at"], cfg.tz)
            item = {
                "day": dt.day,
                "month": MONTHS[dt.month - 1],
                "weekday": WEEKDAYS[dt.weekday()],
                "time": f"{dt:%H:%M}",
                "when": fmt_dt(a["starts_at"], cfg.tz),
                "status": a["status"],
            }
            if a["status"] == "scheduled" and a["starts_at"] > now - 3 * 3600:
                upcoming.append(item | {"ts": a["starts_at"]})
            elif a["status"] == "came":
                past.append(item)
        upcoming.sort(key=lambda x: x["ts"])

        requests = [
            {"id": r["id"], "date": f"{datetime.fromtimestamp(r['created_at'], cfg.tz):%d.%m}", "status": r["status"]}
            for r in await db.user_open_applications(user_id)
        ]
        payments = [
            {"amount": rub(p["amount"]), "status": p["status"]} for p in await db.user_payments(user_id)
        ]
        return web.json_response({
            "name": client["name"] if client else (init.user.first_name or ""),
            "upcoming": upcoming,
            "past": past[:10],
            "requests": requests,
            "payments": payments,
            "aftercare": _plain(await db.get_setting("aftercare") or ""),
        })

    app.router.add_get("/", index)
    app.router.add_get("/api/content", content)
    app.router.add_post("/api/apply", apply)
    app.router.add_post("/api/me", me)
    app.router.add_static("/static", WEBAPP_DIR / "static")
    cfg.uploads_dir.mkdir(parents=True, exist_ok=True)
    app.router.add_static("/uploads", cfg.uploads_dir)
    return app
