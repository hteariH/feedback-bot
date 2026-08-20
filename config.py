"""Настройки бота. Всё, что меняется руками, лежит здесь и в .env."""
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = [
    int(chunk)
    for chunk in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
    if chunk.lstrip("-").isdigit()
]

DB_PATH = os.getenv("DB_PATH", "feedback.db")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))

# Проекты, по которым собираем фидбек.
# Ключ уходит в диплинк: t.me/<бот>?start=<ключ> — по такой ссылке
# проект подставится сам, без выбора в меню.
PROJECTS: dict[str, str] = {
    "wordle": "🟩 Wordle TG",
    "eva": "🤖 Eva Assistant",
    "water": "💧 Water Reminder",
    "colortrap": "🎨 Color Trap",
}

OTHER_PROJECT = "other"
OTHER_PROJECT_TITLE = "💬 Другое"


def project_title(key: str | None) -> str:
    if not key or key == OTHER_PROJECT:
        return OTHER_PROJECT_TITLE
    return PROJECTS.get(key, key)


WELCOME = (
    "Привет! Это канал обратной связи.\n\n"
    "Напиши, что сломалось, чего не хватает или что понравилось — "
    "текстом, скриншотом, голосовым, как удобно. "
    "Я передам всё автору, ответ придёт сюда же."
)

SENT_CONFIRMATION = "Отправлено автору ✅ Ответ придёт сюда."
