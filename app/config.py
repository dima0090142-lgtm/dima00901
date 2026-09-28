import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# Русские буквы, похожие на латинские, — частая опечатка в названиях переменных в панели хостинга
_LOOKALIKES = str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY")


def _normalize_env() -> None:
    """Принимает «bot_token», « BOT_TOKEN » или BOT_TОKEN с русской «О» как BOT_TOKEN."""
    for key, value in list(os.environ.items()):
        name = key.strip().upper().translate(_LOOKALIKES)
        if name != key and name not in os.environ:
            os.environ[name] = value


_normalize_env()

BASE_DIR = Path(__file__).resolve().parent.parent
WEBAPP_DIR = BASE_DIR / "webapp"


def _https(url: str) -> str:
    """Telegram принимает только https-ссылки — дописываем схему, если её забыли."""
    url = url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.replace("http://", "https://", 1)


def _int_list(value: str) -> list[int]:
    return [int(x) for x in re.findall(r"-?\d+", value)]


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: list[int]
    webapp_url: str
    channel_id: str
    miniapp_link: str
    data_dir: Path
    port: int
    tz: ZoneInfo
    master_name: str
    arrival_check_minutes: int
    telegram_proxy: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            # Печатаем только названия переменных (без значений), чтобы было видно опечатку
            names = sorted(k for k in os.environ if not k.startswith(("PYTHON", "LC_", "LANG", "GPG")))
            raise RuntimeError(
                "Не задан BOT_TOKEN. Добавьте переменную BOT_TOKEN (на запуск) и перезапустите приложение. "
                f"Переменные, которые видит приложение: {', '.join(names)}"
            )
        return cls(
            bot_token=token,
            admin_ids=_int_list(os.getenv("ADMIN_IDS", "")),
            webapp_url=_https(os.getenv("WEBAPP_URL", "")),
            channel_id=os.getenv("CHANNEL_ID", "").strip(),
            miniapp_link=os.getenv("MINIAPP_LINK", "").strip(),
            data_dir=Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))),
            port=int(os.getenv("PORT", "8080")),
            tz=ZoneInfo(os.getenv("TIMEZONE", "Asia/Vladivostok")),
            master_name=os.getenv("MASTER_NAME", "Дарья"),
            arrival_check_minutes=int(os.getenv("ARRIVAL_CHECK_MINUTES", "30")),
            telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip(),
        )
