import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
WEBAPP_DIR = BASE_DIR / "webapp"


def _int_list(value: str) -> list[int]:
    return [int(x) for x in value.replace(";", ",").split(",") if x.strip()]


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
            raise RuntimeError("Не задан BOT_TOKEN (см. .env.example)")
        return cls(
            bot_token=token,
            admin_ids=_int_list(os.getenv("ADMIN_IDS", "")),
            webapp_url=os.getenv("WEBAPP_URL", "").strip().rstrip("/"),
            channel_id=os.getenv("CHANNEL_ID", "").strip(),
            miniapp_link=os.getenv("MINIAPP_LINK", "").strip(),
            data_dir=Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))),
            port=int(os.getenv("PORT", "8080")),
            tz=ZoneInfo(os.getenv("TIMEZONE", "Asia/Vladivostok")),
            master_name=os.getenv("MASTER_NAME", "Дарья"),
            arrival_check_minutes=int(os.getenv("ARRIVAL_CHECK_MINUTES", "30")),
            telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip(),
        )
