import re
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

STATUS_LABELS = {
    "new": "🆕 новая",
    "contacted": "📞 связались",
    "scheduled": "📅 записан(а) на сеанс",
    "rejected": "❌ отклонена",
}

MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def fmt_dt(ts: int, tz: ZoneInfo) -> str:
    dt = datetime.fromtimestamp(ts, tz)
    return f"{dt.day} {MONTHS[dt.month - 1]} ({WEEKDAYS[dt.weekday()]}) в {dt:%H:%M}"


def parse_dt(text: str, tz: ZoneInfo, now: datetime | None = None) -> datetime | None:
    """Разбирает «25.10 14:00» или «25.10.2026 14:00». Без года — ближайшая такая дата."""
    m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?\s+(\d{1,2})[:.](\d{2})\s*", text or "")
    if not m:
        return None
    day, month, year, hour, minute = m.groups()
    now = now or datetime.now(tz)
    y = int(year) if year else now.year
    if y < 100:
        y += 2000
    try:
        dt = datetime(y, int(month), int(day), int(hour), int(minute), tzinfo=tz)
    except ValueError:
        return None
    if not year and dt < now - timedelta(days=1):
        dt = dt.replace(year=y + 1)
    return dt


def application_text(app: dict, tz: ZoneInfo) -> str:
    created = datetime.fromtimestamp(app["created_at"], tz)
    return (
        f"<b>Заявка №{app['id']}</b> · {STATUS_LABELS.get(app['status'], app['status'])}\n"
        f"🕒 {created:%d.%m.%Y %H:%M}\n\n"
        f"👤 <b>Имя:</b> {escape(app['name'])}\n"
        f"📱 <b>Телефон:</b> {escape(app['phone'])}\n"
        f"💬 <b>Идея:</b> {escape(app['idea']) or '—'}\n\n"
        f'<a href="tg://user?id={app["user_id"]}">Написать клиенту в Telegram</a>'
    )


def application_kb(app: dict) -> InlineKeyboardMarkup:
    rows = []
    if app["status"] in ("new", "contacted"):
        rows.append([InlineKeyboardButton(text="📅 Назначить сеанс", callback_data=f"app:sched:{app['id']}")])
        second = []
        if app["status"] == "new":
            second.append(InlineKeyboardButton(text="📞 Связалась", callback_data=f"app:contact:{app['id']}"))
        second.append(InlineKeyboardButton(text="❌ Отклонить", callback_data=f"app:reject:{app['id']}"))
        rows.append(second)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def booking_kb(webapp_url: str) -> InlineKeyboardMarkup | None:
    if not webapp_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✍️ Записаться на консультацию", web_app=WebAppInfo(url=webapp_url))
    ]])
