"""ИИ-помощник для канала: пишет тексты постов и предлагает идеи.

Работает через любой OpenAI-совместимый AI-шлюз (например, NordRouter): нужны адрес API, ключ и модель.
Без них бот обходится банком готовых идей.
"""
import base64
import logging
import random
from datetime import datetime

import aiohttp

from .config import Config
from .db import Database

log = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=120)

STYLE = """Ты — SMM-помощник тату-студии «Тату-Культ» (Владивосток). Пишешь посты для Telegram-канала студии.

Правила:
- Пиши по-русски, тепло, живо и лаконично, от лица студии («мы») или мастера, без канцелярита и штампов.
- Длина поста — до 700 символов: 2–4 коротких абзаца, 1–3 уместных эмодзи.
- В конце — мягкий призыв записаться на консультацию через бота и 3–5 хэштегов (например #татувладивосток #тату).
- Никогда не выдумывай цены, скидки, сроки, даты и факты, которых нет во вводных. Если чего-то не знаешь — просто не упоминай.
- Не используй Markdown и HTML-разметку: только обычный текст и эмодзи.
- Ответ — только текст поста, без пояснений до или после."""


class AIError(Exception):
    """Понятная админу причина, почему ИИ не ответил."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ContentAI:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.url = cfg.ai_base_url.rstrip("/") + "/chat/completions" if cfg.ai_base_url else ""

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.cfg.ai_api_key and self.cfg.ai_model)

    async def _context(self) -> str:
        """Сведения о студии и последние посты — чтобы ИИ писал в том же духе и не повторялся."""
        about = await self.db.get_setting("about") or ""
        address = await self.db.get_setting("address") or ""
        promos = [p["text"] for p in await self.db.list_promos()]
        recent = await self.db.recent_post_texts(5)
        today = datetime.now(self.cfg.tz)
        parts = [
            f"Мастер: {self.cfg.master_name}. Адрес: {address}.",
            f"О студии: {about}",
            f"Сегодня: {today:%d.%m.%Y}, {['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье'][today.weekday()]}.",
        ]
        if promos:
            parts.append("Действующие акции:\n" + "\n".join(f"- {p}" for p in promos))
        if recent:
            parts.append("Последние посты канала (не повторяйся):\n" + "\n---\n".join(r[:400] for r in recent))
        return "\n\n".join(parts)

    async def _request(self, content: list[dict]) -> str:
        payload = {
            "model": self.cfg.ai_model,
            "messages": [{"role": "system", "content": STYLE}, {"role": "user", "content": content}],
            "max_tokens": 2000,
        }
        headers = {"Authorization": f"Bearer {self.cfg.ai_api_key}"}
        try:
            # trust_env — чтобы учитывались HTTPS_PROXY и подобные настройки сервера, если они есть
            async with aiohttp.ClientSession(timeout=TIMEOUT, trust_env=True) as session:
                async with session.post(self.url, json=payload, headers=headers) as resp:
                    body = await resp.json(content_type=None)
                    status = resp.status
        except (aiohttp.ClientError, TimeoutError) as e:
            log.warning("AI gateway connection error: %s", e)
            raise AIError("Не удалось подключиться к AI-шлюзу — проверьте AI_BASE_URL.")
        except ValueError:
            raise AIError("AI-шлюз вернул непонятный ответ — проверьте AI_BASE_URL.")

        if status != 200:
            detail = ""
            if isinstance(body, dict):
                err = body.get("error")
                detail = (err.get("message") if isinstance(err, dict) else err) or body.get("message") or ""
            log.warning("AI gateway error %s: %s", status, detail)
            raise AIError({
                400: f"Шлюз отклонил запрос: {detail or 'проверьте AI_MODEL'}",
                401: "Ключ AI_API_KEY не подходит — проверьте его.",
                402: "На балансе AI-шлюза закончились средства.",
                403: "Доступ запрещён для этого ключа или модели.",
                404: "Модель или адрес не найдены — проверьте AI_MODEL и AI_BASE_URL.",
                429: "Слишком много запросов к ИИ — попробуйте через минуту.",
            }.get(status, "ИИ временно недоступен — попробуйте позже."), status)

        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise AIError("AI-шлюз вернул ответ в неожиданном формате.")
        text = message.get("content") or ""
        if isinstance(text, list):  # некоторые шлюзы отдают список частей
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        text = text.strip()
        if not text:
            raise AIError("ИИ вернул пустой ответ — попробуйте ещё раз.")
        return text

    async def _ask(self, content: list[dict]) -> str:
        if not self.enabled:
            raise AIError("ИИ не подключён: задайте AI_BASE_URL, AI_API_KEY и AI_MODEL в настройках сервера.")
        try:
            return await self._request(content)
        except AIError as e:
            # Не все модели понимают картинки — тогда пробуем ещё раз только с текстом
            if e.status != 400 or not any(part.get("type") == "image_url" for part in content):
                raise
            log.info("Модель не приняла фото, повторяю запрос без него")
            return await self._request([part for part in content if part.get("type") != "image_url"])

    async def write_post(self, brief: str, photo: bytes | None = None) -> str:
        content: list[dict] = []
        if photo:
            content.append({
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.standard_b64encode(photo).decode()},
            })
        task = "Напиши пост для канала."
        if photo:
            task += " На фото — работа мастера: опиши её живо (стиль, настроение, место на теле), если это видно."
        if brief:
            task += f"\n\nВводные от администратора:\n{brief}"
        content.append({"type": "text", "text": f"{await self._context()}\n\n{task}"})
        return (await self._ask(content))[:1000]

    async def ideas(self) -> list[str]:
        prompt = (
            f"{await self._context()}\n\n"
            "Предложи 5 разных идей для ближайших постов канала. Учитывай сезон, праздники ближайших недель, "
            "акции и то, о чём уже писали. Каждая идея — одна строка до 120 символов, без нумерации и пояснений."
        )
        text = await self._ask([{"type": "text", "text": prompt}])
        ideas = [line.strip(" -•*0123456789.)\t") for line in text.splitlines()]
        return [i for i in ideas if len(i) > 5][:5]


# Банк идей на случай, если ИИ не подключён
IDEA_BANK = [
    "Покажите свежую работу крупным планом и расскажите её историю",
    "Эскиз → готовая татуировка: две фотографии «до и после»",
    "Как подготовиться к первому сеансу: 5 простых советов",
    "Уход за татуировкой в первые две недели — короткая памятка",
    "Больно ли делать тату: развеиваем мифы",
    "Знакомство с мастером: как Дарья пришла в тату",
    "Свободные окна на ближайшую неделю — приглашение записаться",
    "Отзыв клиента с фото зажившей работы",
    "Какие стили татуировок сейчас выбирают чаще всего",
    "Маленькие тату для первого раза: подборка идей",
    "Как мы стерилизуем инструменты и почему это важно",
    "Закулисье студии: рабочее место и материалы",
    "Как выбрать место на теле для первой татуировки",
    "Перекрытие старой татуировки: что возможно, а что нет",
    "Вопрос–ответ: самые частые вопросы на консультациях",
    "Татуировка к празднику или памятной дате — идеи подарка",
    "Процесс создания эскиза: от идеи клиента до рисунка",
    "Как зажившая татуировка выглядит через месяц",
]


def random_ideas(n: int = 5) -> list[str]:
    return random.sample(IDEA_BANK, n)
