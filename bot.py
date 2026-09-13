import os
import html
import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not ADMIN_ID_RAW:
    raise RuntimeError("ADMIN_ID is missing")

if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is missing")

if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL is missing")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError("ADMIN_ID must be a number")


DB_NAME = "bot.db"
WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================
# DATABASE
# =========================

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_map (
                admin_message_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS blocked_users (
                user_id INTEGER PRIMARY KEY
            )
        """)

        await db.commit()


# =========================
# USER FUNCTIONS
# =========================

async def save_user(message: Message):
    user = message.from_user

    if not user:
        return False

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            "SELECT user_id FROM blocked_users WHERE user_id = ?",
            (user.id,)
        )

        if await cursor.fetchone():
            return False

        await db.execute("""
            INSERT INTO users (user_id, name, username)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                name = excluded.name,
                username = excluded.username
        """, (
            user.id,
            user.full_name,
            user.username
        ))

        await db.commit()

    return True


async def manual_add_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute(
            "DELETE FROM blocked_users WHERE user_id = ?",
            (user_id,)
        )

        await db.execute("""
            INSERT OR IGNORE INTO users
            (user_id, name, username)
            VALUES (?, ?, ?)
        """, (
            user_id,
            "Manually Added",
            None
        ))

        await db.commit()


async def manual_remove_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute(
            "DELETE FROM users WHERE user_id = ?",
            (user_id,)
        )

        await db.execute(
            "INSERT OR IGNORE INTO blocked_users (user_id) VALUES (?)",
            (user_id,)
        )

        await db.execute(
            "DELETE FROM message_map WHERE user_id = ?",
            (user_id,)
        )

        await db.commit()


async def get_users():
    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT user_id, name, username
            FROM users
            ORDER BY added_at DESC
        """)

        return await cursor.fetchall()


# =========================
# MESSAGE MAP
# =========================

async def save_mapping(admin_message_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
            INSERT OR REPLACE INTO message_map
            (admin_message_id, user_id)
            VALUES (?, ?)
        """, (
            admin_message_id,
            user_id
        ))

        await db.commit()


async def get_user_from_message(admin_message_id: int):
    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT user_id
            FROM message_map
            WHERE admin_message_id = ?
        """, (admin_message_id,))

        row = await cursor.fetchone()

        return row[0] if row else None


# =========================
# MAIN HANDLER
# =========================

@dp.message()
async def message_handler(message: Message):

    if not message.from_user:
        return

    sender_id = message.from_user.id

    # =====================
    # ADMIN
    # =====================

    if sender_id == ADMIN_ID:

        text = message.text or ""

        # /adduser
        if text.startswith("/adduser"):

            parts = text.split()

            if len(parts) != 2:
                await message.answer(
                    "❌ Usage:\n/adduser USER_ID"
                )
                return

            try:
                target_id = int(parts[1])
            except ValueError:
                await message.answer(
                    "❌ User ID must be a number."
                )
                return

            await manual_add_user(target_id)

            await message.answer(
                "✅ User added.\n\n"
                f"🆔 ID: <code>{target_id}</code>",
                parse_mode="HTML"
            )
            return

        # /removeuser
        if text.startswith("/removeuser"):

            parts = text.split()

            if len(parts) != 2:
                await message.answer(
                    "❌ Usage:\n/removeuser USER_ID"
                )
                return

            try:
                target_id = int(parts[1])
            except ValueError:
                await message.answer(
                    "❌ User ID must be a number."
                )
                return

            await manual_remove_user(target_id)

            await message.answer(
                "🗑️ User removed and blocked.\n\n"
                f"🆔 ID: <code>{target_id}</code>",
                parse_mode="HTML"
            )
            return

        # /users
        if text.strip() == "/users":

            users = await get_users()

            if not users:
                await message.answer("📭 No users found.")
                return

            lines = ["👥 <b>Users</b>\n"]

            for number, user in enumerate(users, start=1):

                uid = user[0]
                name = user[1] or "Unknown"

                username = (
                    f"@{user[2]}"
                    if user[2]
                    else "No username"
                )

                lines.append(
                    f"{number}. 👤 {html.escape(name)}\n"
                    f"   🔹 {html.escape(username)}\n"
                    f"   🆔 <code>{uid}</code>\n"
                )

            result = "\n".join(lines)

            if len(result) > 4000:
                result = result[:4000] + "\n\n⚠️ List truncated."

            await message.answer(
                result,
                parse_mode="HTML"
            )
            return

        # =====================
        # ADMIN REPLY
        # =====================

        if not message.reply_to_message:
            await message.answer(
                "↩️ Reply to a user's forwarded message."
            )
            return

        replied_id = message.reply_to_message.message_id

        target_user_id = await get_user_from_message(replied_id)

        if not target_user_id:
            await message.answer(
                "❌ User mapping not found."
            )
            return

        try:

            # TEXT
            if message.text:
                await bot.send_message(
                    chat_id=target_user_id,
                    text=message.text
                )

            # PHOTO
            elif message.photo:
                await bot.send_photo(
                    chat_id=target_user_id,
                    photo=message.photo[-1].file_id,
                    caption=message.caption
                )

            # VIDEO
            elif message.video:
                await bot.send_video(
                    chat_id=target_user_id,
                    video=message.video.file_id,
                    caption=message.caption
                )

            # DOCUMENT
            elif message.document:
                await bot.send_document(
                    chat_id=target_user_id,
                    document=message.document.file_id,
                    caption=message.caption
                )

            # VOICE
            elif message.voice:
                await bot.send_voice(
                    chat_id=target_user_id,
                    voice=message.voice.file_id
                )

            # AUDIO
            elif message.audio:
                await bot.send_audio(
                    chat_id=target_user_id,
                    audio=message.audio.file_id,
                    caption=message.caption
                )

            # STICKER
            elif message.sticker:
                await bot.send_sticker(
                    chat_id=target_user_id,
                    sticker=message.sticker.file_id
                )

            # GIF
            elif message.animation:
                await bot.send_animation(
                    chat_id=target_user_id,
                    animation=message.animation.file_id,
                    caption=message.caption
                )

            else:
                await message.answer(
                    "⚠️ This message type is not supported."
                )
                return

            # IMPORTANT:
            # This confirmation goes ONLY to ADMIN.
            # Nothing extra is sent to the user.

            await message.answer("✅ Sent.")

        except Exception as e:

            print("Admin -> User error:", e)

            await message.answer(
                "❌ Failed to send reply."
            )

        return

    # =====================
    # USER -> ADMIN
    # =====================

    try:

        allowed = await save_user(message)

        if not allowed:
            return

        user = message.from_user

        username = (
            f"@{user.username}"
            if user.username
            else "No username"
        )

        info = await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "📩 <b>New User Message</b>\n\n"
                f"👤 Name: {html.escape(user.full_name)}\n"
                f"🔹 Username: {html.escape(username)}\n"
                f"🆔 User ID: <code>{user.id}</code>\n\n"
                "↩️ Reply to the forwarded message."
            ),
            parse_mode="HTML"
        )

        forwarded = await message.forward(
            chat_id=ADMIN_ID
        )

        # Both messages can be replied to by admin
        await save_mapping(info.message_id, user.id)
        await save_mapping(forwarded.message_id, user.id)

    except Exception as e:

        print("User -> Admin error:", e)


# =========================
# HEALTH CHECK
# =========================

async def health_check(request):
    return web.Response(
        text="Telegram bot is running."
    )


# =========================
# STARTUP
# =========================

async def on_startup(bot: Bot):

    await init_db()

    await bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )

    print("Webhook set:", WEBHOOK_URL)


# =========================
# WEB APP
# =========================

app = web.Application()

app.router.add_get(
    "/",
    health_check
)

webhook_handler = SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
    secret_token=WEBHOOK_SECRET
)

webhook_handler.register(
    app,
    path=WEBHOOK_PATH
)

setup_application(
    app,
    dp,
    bot=bot
)

dp.startup.register(on_startup)


# =========================
# RUN
# =========================

if __name__ == "__main__":

    port = int(
        os.getenv("PORT", "10000")
    )

    web.run_app(
        app,
        host="0.0.0.0",
        port=port
    )
