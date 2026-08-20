"""Бот обратной связи: пользователь пишет боту — сообщение падает админу,
админ отвечает реплаем — ответ уходит обратно пользователю."""
import asyncio
import csv
import html
import io
import logging
import sys
import time
from collections import defaultdict, deque

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
    ReplyParameters,
)

import config
import db

# Консоль Windows по умолчанию cp1251/cp1252 — без этого логи с эмодзи падают.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("feedback-bot")

admin_router = Router(name="admin")
user_router = Router(name="user")

_rate: dict[int, deque[float]] = defaultdict(deque)


def rate_limited(user_id: int) -> bool:
    """Простой счётчик сообщений в минуту, чтобы бота не залили спамом."""
    window = _rate[user_id]
    now = time.monotonic()
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= config.RATE_LIMIT_PER_MINUTE:
        return True
    window.append(now)
    return False


def projects_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"proj:{key}")]
        for key, title in config.PROJECTS.items()
    ]
    rows.append([
        InlineKeyboardButton(
            text=config.OTHER_PROJECT_TITLE,
            callback_data=f"proj:{config.OTHER_PROJECT}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_header(user, project: str | None) -> str:
    name = html.escape(user.full_name or str(user.id))
    handle = f" @{html.escape(user.username)}" if user.username else ""
    return (
        f"👤 <b>{name}</b>{handle} · <code>{user.id}</code>\n"
        f"📦 {html.escape(config.project_title(project))}"
    )


def body_html(message: Message) -> str | None:
    """Текст или подпись сообщения с сохранённым форматированием."""
    if message.text is not None or message.caption is not None:
        return message.html_text
    return None


async def confirm(message: Message, emoji: str = "👌") -> None:
    """Тихое подтверждение реакцией; если реакции недоступны — короткий ответ."""
    try:
        await message.react([ReactionTypeEmoji(emoji=emoji)])
    except TelegramAPIError:
        await message.reply("✅")


# ─────────────────────────── пользователь ───────────────────────────


@user_router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    user = db.touch_user(
        message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    payload = (command.args or "").strip()

    if payload in config.PROJECTS:
        db.set_project(user["user_id"], payload)
        await message.answer(
            f"{config.WELCOME}\n\nПроект: <b>{html.escape(config.project_title(payload))}</b>"
        )
        return

    await message.answer(config.WELCOME)
    await message.answer("По какому проекту пишешь?", reply_markup=projects_keyboard())


@user_router.message(Command("project"))
async def cmd_project(message: Message) -> None:
    db.touch_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer("Выбери проект:", reply_markup=projects_keyboard())


@user_router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Твой id: <code>{message.from_user.id}</code>")


@user_router.callback_query(F.data.startswith("proj:"))
async def pick_project(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    db.touch_user(
        callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    db.set_project(callback.from_user.id, key)
    title = html.escape(config.project_title(key))
    await callback.message.edit_text(f"Проект: <b>{title}</b>\n\nТеперь пиши — я передам.")
    await callback.answer()


@user_router.message()
async def collect_feedback(message: Message, bot: Bot) -> None:
    user = db.touch_user(
        message.from_user.id, message.from_user.username, message.from_user.full_name
    )

    if user["banned"]:
        return
    if rate_limited(user["user_id"]):
        await message.answer("Слишком много сообщений подряд — подожди минуту 🙏")
        return
    if not config.ADMIN_IDS:
        await message.answer("Бот ещё не настроен: не задан ADMIN_IDS.")
        log.warning("ADMIN_IDS пуст — сообщение от %s некуда отправить", user["user_id"])
        return

    project = user["project"]
    header = user_header(message.from_user, project)
    text = body_html(message)
    delivered = False

    for admin_id in config.ADMIN_IDS:
        try:
            if message.text is not None:
                sent = await bot.send_message(
                    admin_id, f"{header}\n\n<blockquote>{text}</blockquote>"
                )
                db.link(admin_id, sent.message_id, user["user_id"], message.message_id)
            else:
                head = await bot.send_message(admin_id, header)
                copied = await message.copy_to(admin_id)
                # Ответить можно и на шапку, и на само вложение.
                db.link(admin_id, head.message_id, user["user_id"], message.message_id)
                db.link(admin_id, copied.message_id, user["user_id"], message.message_id)
            delivered = True
        except TelegramForbiddenError:
            log.warning("Админ %s не запускал бота — доставить не могу", admin_id)
        except TelegramAPIError:
            log.exception("Не удалось доставить сообщение админу %s", admin_id)

    if not delivered:
        await message.answer("Не получилось доставить сообщение, попробуй позже 🙏")
        return

    db.bump_messages(user["user_id"])
    db.log_message(user["user_id"], project, message.content_type, text, "in")
    await message.answer(config.SENT_CONFIRMATION)


# ───────────────────────────── админ ─────────────────────────────

ADMIN_HELP = (
    "<b>Бот обратной связи</b>\n\n"
    "Отвечай <b>реплаем</b> на сообщение от пользователя — ответ уйдёт ему от имени бота.\n\n"
    "/stats — статистика\n"
    "/users [N] — последние пользователи\n"
    "/say &lt;user_id&gt; &lt;текст&gt; — написать без реплая\n"
    "/ban — реплаем на сообщение (или /ban &lt;user_id&gt;)\n"
    "/unban &lt;user_id&gt;\n"
    "/export — выгрузка всего фидбека в CSV\n"
    "/id — id этого чата"
)


@admin_router.message(Command("start", "help"))
async def admin_help(message: Message) -> None:
    await message.answer(ADMIN_HELP)


@admin_router.message(Command("id"))
async def admin_id(message: Message) -> None:
    await message.answer(f"Этот чат: <code>{message.chat.id}</code>")


@admin_router.message(Command("stats"))
async def admin_stats(message: Message) -> None:
    s = db.stats()
    lines = [
        "<b>Статистика</b>",
        f"Пользователей: {s['users']} (в бане: {s['banned']})",
        f"Входящих: {s['incoming']} · за сутки: {s['last_24h']}",
        f"Отправлено ответов: {s['outgoing']}",
    ]
    if s["by_project"]:
        lines.append("\n<b>По проектам</b>")
        for row in s["by_project"]:
            lines.append(f"· {html.escape(config.project_title(row['project']))} — {row['n']}")
    await message.answer("\n".join(lines))


@admin_router.message(Command("users"))
async def admin_users(message: Message, command: CommandObject) -> None:
    limit = 15
    if command.args and command.args.strip().isdigit():
        limit = max(1, min(50, int(command.args.strip())))
    rows = db.recent_users(limit)
    if not rows:
        await message.answer("Пока никто не писал.")
        return
    lines = ["<b>Последние пользователи</b>"]
    for row in rows:
        handle = f" @{html.escape(row['username'])}" if row["username"] else ""
        flag = " 🚫" if row["banned"] else ""
        lines.append(
            f"· <b>{html.escape(row['full_name'] or '—')}</b>{handle} — "
            f"<code>{row['user_id']}</code> · {row['msg_count']} сообщ. · "
            f"{html.escape(config.project_title(row['project']))}{flag}"
        )
    await message.answer("\n".join(lines))


@admin_router.message(Command("say"))
async def admin_say(message: Message, command: CommandObject, bot: Bot) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[0].lstrip("-").isdigit():
        await message.answer("Формат: <code>/say &lt;user_id&gt; текст</code>")
        return
    user_id, text = int(parts[0]), parts[1]
    try:
        await bot.send_message(user_id, text)
    except TelegramForbiddenError:
        await message.answer("Пользователь заблокировал бота.")
        return
    except TelegramAPIError as err:
        await message.answer(f"Не отправилось: {html.escape(str(err))}")
        return
    db.log_message(user_id, None, "text", text, "out")
    await confirm(message)


@admin_router.message(Command("ban"))
async def admin_ban(message: Message, command: CommandObject) -> None:
    user_id = None
    if command.args and command.args.strip().lstrip("-").isdigit():
        user_id = int(command.args.strip())
    elif message.reply_to_message:
        thread = db.resolve(message.chat.id, message.reply_to_message.message_id)
        if thread:
            user_id = thread["user_id"]
    if user_id is None:
        await message.answer("Ответь этой командой на сообщение пользователя или укажи id.")
        return
    db.set_banned(user_id, True)
    await message.answer(f"🚫 <code>{user_id}</code> забанен — его сообщения больше не приходят.")


@admin_router.message(Command("unban"))
async def admin_unban(message: Message, command: CommandObject) -> None:
    if not command.args or not command.args.strip().lstrip("-").isdigit():
        await message.answer("Формат: <code>/unban &lt;user_id&gt;</code>")
        return
    user_id = int(command.args.strip())
    db.set_banned(user_id, False)
    await message.answer(f"✅ <code>{user_id}</code> разбанен.")


@admin_router.message(Command("export"))
async def admin_export(message: Message) -> None:
    rows = db.export_rows()
    if not rows:
        await message.answer("Пока нечего выгружать.")
        return
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["created_at", "direction", "user_id", "username", "full_name", "project", "kind", "text"]
    )
    writer.writerows([tuple(row) for row in rows])
    payload = buffer.getvalue().encode("utf-8-sig")
    await message.answer_document(
        BufferedInputFile(payload, filename="feedback.csv"),
        caption=f"Выгрузка: {len(rows)} записей",
    )


@admin_router.message(F.reply_to_message)
async def admin_reply(message: Message) -> None:
    thread = db.resolve(message.chat.id, message.reply_to_message.message_id)
    if not thread:
        await message.reply(
            "Не знаю, кому это адресовано — ответь реплаем на сообщение от пользователя."
        )
        return
    try:
        await message.copy_to(
            thread["user_id"],
            reply_parameters=ReplyParameters(
                message_id=thread["user_msg_id"], allow_sending_without_reply=True
            ),
        )
    except TelegramForbiddenError:
        await message.reply("Пользователь заблокировал бота — ответ не доставлен.")
        return
    except TelegramAPIError as err:
        await message.reply(f"Не отправилось: {html.escape(str(err))}")
        return
    db.log_message(thread["user_id"], None, message.content_type, body_html(message), "out")
    await confirm(message)


@admin_router.message()
async def admin_hint(message: Message) -> None:
    await message.answer(
        "Это админ-чат бота. Чтобы ответить пользователю — реплай на его сообщение. "
        "/help — список команд."
    )


async def setup_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать"),
            BotCommand(command="project", description="Выбрать проект"),
        ],
        scope=BotCommandScopeDefault(),
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="help", description="Команды"),
                    BotCommand(command="stats", description="Статистика"),
                    BotCommand(command="users", description="Последние пользователи"),
                    BotCommand(command="export", description="Выгрузка CSV"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except TelegramAPIError:
            log.warning("Не удалось поставить команды админу %s (он не запускал бота?)", admin_id)


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN — скопируй .env.example в .env и заполни.")

    db.connect()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    # Порядок важен: сначала админский роутер, иначе ответы админа уйдут в сбор фидбека.
    admin_router.message.filter(F.chat.id.in_(config.ADMIN_IDS))
    dp.include_router(admin_router)
    dp.include_router(user_router)

    me = await bot.get_me()
    log.info("Запущен как @%s, админы: %s", me.username, config.ADMIN_IDS or "НЕ ЗАДАНЫ")
    for key in config.PROJECTS:
        log.info("Диплинк %s → https://t.me/%s?start=%s", key, me.username, key)

    await setup_commands(bot)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Остановлен")
