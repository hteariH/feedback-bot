"""Прогон настоящих апдейтов через диспетчер с подменённым Bot.__call__ — без сети и без токена.

Запуск: python tests/test_flow.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["BOT_TOKEN"] = "123456789:AAHfake-token-for-offline-test-xxxx"
os.environ["ADMIN_IDS"] = "777"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.methods import (  # noqa: E402
    CopyMessage,
    GetMe,
    SendDocument,
    SendMessage,
    SetMessageReaction,
)
from aiogram.types import Chat, Message, MessageId, PhotoSize, Update, User  # noqa: E402

import bot as app  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ADMIN = 777
USER = 555

calls: list = []
_next_id = [1000]


async def fake_call(self, method, request_timeout=None):
    """Вместо HTTP к Telegram — запоминаем вызов и возвращаем правдоподобный ответ."""
    calls.append(method)
    if isinstance(method, GetMe):
        return User(id=1, is_bot=True, first_name="Feedback", username="feedback_bot")
    if isinstance(method, SendMessage):
        _next_id[0] += 1
        return Message(
            message_id=_next_id[0],
            date=NOW,
            chat=Chat(id=method.chat_id, type="private"),
            text=method.text,
        )
    if isinstance(method, CopyMessage):
        _next_id[0] += 1
        return MessageId(message_id=_next_id[0])
    if isinstance(method, SetMessageReaction):
        return True
    return True


Bot.__call__ = fake_call


def incoming(user_id, text, msg_id, chat_id=None, reply_to=None):
    user = User(id=user_id, is_bot=False, first_name="Вася", username="vasya")
    msg = Message(
        message_id=msg_id,
        date=NOW,
        chat=Chat(id=chat_id or user_id, type="private"),
        from_user=user,
        text=text,
        reply_to_message=reply_to,
    )
    return Update(update_id=msg_id, message=msg)


def sends_to(chat_id):
    return [c for c in calls if isinstance(c, SendMessage) and c.chat_id == chat_id]


def copies_to(chat_id):
    return [c for c in calls if isinstance(c, CopyMessage) and c.chat_id == chat_id]


async def main() -> None:
    db.connect()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    app.admin_router.message.filter(F.chat.id.in_(config.ADMIN_IDS))
    dp.include_router(app.admin_router)
    dp.include_router(app.user_router)

    # 1. /start с диплинком проставляет проект.
    await dp.feed_update(bot, incoming(USER, "/start wordle", 1))
    assert db.get_user(USER)["project"] == "wordle"
    print("1. диплинк /start ok")

    # 2. Фидбек уходит админу: шапка, проект, экранированный текст, связка в базе.
    calls.clear()
    await dp.feed_update(bot, incoming(USER, "Слово не засчиталось <3", 2))
    to_admin = sends_to(ADMIN)
    assert len(to_admin) == 1, calls
    assert "vasya" in to_admin[0].text and "Wordle" in to_admin[0].text
    assert "&lt;3" in to_admin[0].text, to_admin[0].text
    thread = db.connect().execute(
        "SELECT * FROM threads WHERE user_id = ?", (USER,)
    ).fetchone()
    assert thread and thread["user_msg_id"] == 2
    admin_msg_id = thread["admin_msg_id"]
    assert db.resolve(ADMIN, admin_msg_id)["user_id"] == USER
    print("2. фидбек доставлен админу ok")

    # 3. Реплай админа уходит пользователю копией и цепляется к его сообщению.
    calls.clear()
    admin_view = Message(
        message_id=admin_msg_id, date=NOW, chat=Chat(id=ADMIN, type="private"), text="header"
    )
    await dp.feed_update(
        bot, incoming(ADMIN, "Починил, спасибо!", 3, chat_id=ADMIN, reply_to=admin_view)
    )
    replies = copies_to(USER)
    assert len(replies) == 1, calls
    assert replies[0].reply_parameters.message_id == 2
    print("3. ответ реплаем ok")

    # 4. Реплай не на тред никуда не уходит.
    calls.clear()
    stray = Message(message_id=99999, date=NOW, chat=Chat(id=ADMIN, type="private"), text="?")
    await dp.feed_update(bot, incoming(ADMIN, "кому это", 4, chat_id=ADMIN, reply_to=stray))
    assert not copies_to(USER)
    assert "Не знаю, кому" in sends_to(ADMIN)[-1].text
    print("4. реплай в пустоту ok")

    # 5. Статистика считается.
    calls.clear()
    await dp.feed_update(bot, incoming(ADMIN, "/stats", 5, chat_id=ADMIN))
    stats_text = sends_to(ADMIN)[-1].text
    assert "Пользователей: 1" in stats_text and "Wordle" in stats_text, stats_text
    print("5. /stats ok")

    # 6. Бан реплаем: сообщения пользователя перестают доходить.
    calls.clear()
    await dp.feed_update(bot, incoming(ADMIN, "/ban", 6, chat_id=ADMIN, reply_to=admin_view))
    assert db.get_user(USER)["banned"] == 1
    calls.clear()
    await dp.feed_update(bot, incoming(USER, "ещё раз", 7))
    assert not sends_to(ADMIN), calls
    print("6. бан ok")

    # 7. Разбан и /say без реплая.
    await dp.feed_update(bot, incoming(ADMIN, "/unban 555", 8, chat_id=ADMIN))
    assert db.get_user(USER)["banned"] == 0
    calls.clear()
    await dp.feed_update(bot, incoming(ADMIN, "/say 555 привет из say", 9, chat_id=ADMIN))
    assert sends_to(USER)[0].text == "привет из say", calls
    print("7. /unban и /say ok")

    # 8. Лимит частоты отбивает поток сообщений.
    calls.clear()
    for i in range(config.RATE_LIMIT_PER_MINUTE + 3):
        await dp.feed_update(bot, incoming(USER, f"спам {i}", 100 + i))
    warned = [c for c in sends_to(USER) if "подожди минуту" in (c.text or "")]
    assert warned, "лимит частоты не сработал"
    print(f"8. лимит частоты ok (отбито {len(warned)})")

    # 9. Вложение: шапка + копия, обе связаны с автором.
    calls.clear()
    app._rate.clear()  # предыдущий шаг намеренно исчерпал лимит
    media = Message(
        message_id=200,
        date=NOW,
        chat=Chat(id=USER, type="private"),
        from_user=User(id=USER, is_bot=False, first_name="Вася", username="vasya"),
        photo=[PhotoSize(file_id="f", file_unique_id="u", width=100, height=100, file_size=10)],
        caption="вот скрин",
    )
    await dp.feed_update(bot, Update(update_id=200, message=media))
    links = db.connect().execute(
        "SELECT COUNT(*) FROM threads WHERE user_msg_id = 200"
    ).fetchone()[0]
    assert len(sends_to(ADMIN)) == 1 and len(copies_to(ADMIN)) == 1 and links == 2
    print("9. вложение ok")

    # 10. Выгрузка CSV.
    calls.clear()
    await dp.feed_update(bot, incoming(ADMIN, "/export", 300, chat_id=ADMIN))
    docs = [c for c in calls if isinstance(c, SendDocument)]
    assert docs, calls
    rows = docs[0].document.data.decode("utf-8-sig").strip().splitlines()
    assert rows[0].startswith("created_at") and len(rows) > 1
    print(f"10. /export ok ({len(rows) - 1} записей)")

    print("\nВСЕ ПРОВЕРКИ ПРОШЛИ")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
