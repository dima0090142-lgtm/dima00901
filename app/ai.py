"""ИИ-помощник для канала: пишет тексты постов и предлагает идеи (Claude API).

Работает, только если задан ANTHROPIC_API_KEY. Без ключа бот обходится банком готовых идей.
"""
import base64
import logging
import random
from datetime import datetime

import anthropic

from .config import Config
from .db import Database

log = logging.getLogger(__name__)

# Отдельный запасной путь на случай отказа модели по соображениям безопасности:
# сервер сам перезапустит запрос на рекомендуемой модели (fallbacks="default").
FALLBACK_BETA = "server-side-fallback-2026-07-01"

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


class ContentAI:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key) if cfg.anthropic_api_key else None

    @property
    def enabled(self) -> bool:
        return self.client is not None

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

    async def _ask(self, content: list[dict]) -> str:
        if not self.client:
            raise AIError("ИИ не подключён: добавьте ANTHROPIC_API_KEY в настройках сервера.")
        try:
            response = await self.client.beta.messages.create(
                model=self.cfg.ai_model,
                max_tokens=16000,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                output_config={"effort": "medium"},
                system=STYLE,
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.AuthenticationError:
            raise AIError("Ключ ANTHROPIC_API_KEY не подходит — проверьте его.")
        except anthropic.PermissionDeniedError:
            raise AIError("Доступ к API запрещён для этого ключа или региона сервера.")
        except anthropic.RateLimitError:
            raise AIError("Слишком много запросов к ИИ — попробуйте через минуту.")
        except anthropic.APIConnectionError:
            raise AIError("Сервер не может подключиться к API ИИ.")
        except anthropic.APIStatusError as e:
            log.warning("Claude API error %s: %s", e.status_code, e.message)
            raise AIError("ИИ временно недоступен — попробуйте позже.")

        if response.stop_reason == "refusal":
            raise AIError("ИИ отказался писать этот текст. Попробуйте описать пост по-другому.")
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise AIError("ИИ вернул пустой ответ — попробуйте ещё раз.")
        return text

    async def write_post(self, brief: str, photo: bytes | None = None) -> str:
        content: list[dict] = []
        if photo:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": base64.standard_b64encode(photo).decode()},
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
