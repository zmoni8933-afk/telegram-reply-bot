import os
import html
import asyncio
import aiosqlite

from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)


# ==========================================
# ENVIRONMENT VARIABLES
# ==========================================

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


# ==========================================
# SETTINGS
# ==========================================

DB_NAME = "bot.db"

WEBHOOK_PATH = "/telegram-webhook"

WEBHOOK_URL = (
    f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
)


# ==========================================
# BOT
# ==========================================

bot = Bot(token=BOT_TOKEN)

dp = Dispatcher()


# ==========================================
# DATABASE
# ==========================================

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


# ==========================================
# USER FUNCTIONS
# ==========================================

async def save_user(message: Message):

    user = message.from_user

    if not user:
        return False

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT user_id
            FROM blocked_users
            WHERE user_id = ?
        """, (user.id,))

        blocked = await cursor.fetchone()

        if blocked:
            return False

        await db.execute("""
            INSERT INTO users (
                user_id,
                name,
                username
            )
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

        await db.execute("""
            DELETE FROM blocked_users
            WHERE user_id = ?
        """, (user_id,))

        await db.execute("""
            INSERT OR IGNORE INTO users (
                user_id,
                name,
                username
            )
            VALUES (?, ?, ?)
        """, (
            user_id,
            "Manually Added",
            None
        ))

        await db.commit()


async def manual_remove_user(user_id: int):

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
            DELETE FROM users
            WHERE user_id = ?
        """, (user_id,))

        await db.execute("""
            INSERT OR IGNORE INTO blocked_users (
                user_id
            )
            VALUES (?)
        """, (user_id,))

        await db.execute("""
            DELETE FROM message_map
            WHERE user_id = ?
        """, (user_id,))

        await db.commit()


async def get_users():

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT user_id, name, username
            FROM users
            ORDER BY added_at DESC
        """)

        return await cursor.fetchall()


# ==========================================
# MESSAGE MAPPING
# ==========================================

async def save_mapping(
    admin_message_id: int,
    user_id: int
):

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
            INSERT OR REPLACE INTO message_map (
                admin_message_id,
                user_id
            )
            VALUES (?, ?)
        """, (
            admin_message_id,
            user_id
        ))

        await db.commit()


async def get_user_from_message(
    admin_message_id: int
):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT user_id
            FROM message_map
            WHERE admin_message_id = ?
        """, (admin_message_id,))

        row = await cursor.fetchone()

        return row[0] if row else None


# ==========================================
# MAIN MESSAGE HANDLER
# ==========================================

@dp.message()
async def message_handler(message: Message):

    if not message.from_user:
        return

    sender_id = message.from_user.id


    # ======================================
    # ADMIN
    # ======================================

    if sender_id == ADMIN_ID:

        text = message.text or ""


        # ==================================
        # /adduser
        # ==================================

        if text.startswith("/adduser"):

            parts = text.split()

            if len(parts) != 2:

                await message.answer(
                    "❌ Usage:\n"
                    "/adduser USER_ID"
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


        # ==================================
        # /removeuser
        # ==================================

        if text.startswith("/removeuser"):

            parts = text.split()

            if len(parts) != 2:

                await message.answer(
                    "❌ Usage:\n"
                    "/removeuser USER_ID"
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


        # ==================================
        # /users
        # ==================================

        if text.strip() == "/users":

            users = await get_users()

            if not users:

                await message.answer(
                    "📭 No users found."
                )

                return

            lines = [
                "👥 <b>Users</b>\n"
            ]

            for number, user in enumerate(
                users,
                start=1
            ):

                uid = user[0]

                name = (
                    user[1]
                    or "Unknown"
                )

                username = user[2]

                if username:
                    username_text = (
                        f"@{username}"
                    )
                else:
                    username_text = (
                        "No username"
                    )

                lines.append(
                    f"{number}. "
                    f"👤 {html.escape(name)}\n"
                    f"   🔹 {html.escape(username_text)}\n"
                    f"   🆔 <code>{uid}</code>\n"
                )

            result = "\n".join(lines)

            if len(result) > 4000:

                result = (
                    result[:4000]
                    + "\n\n⚠️ List truncated."
                )

            await message.answer(
                result,
                parse_mode="HTML"
            )

            return


        # ==================================
        # ADMIN -> USER
        # ==================================

        if not message.reply_to_message:

            await message.answer(
                "↩️ Reply to a user's forwarded message."
            )

            return


        replied_message_id = (
            message.reply_to_message.message_id
        )

        target_user_id = (
            await get_user_from_message(
                replied_message_id
            )
        )

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


            # GIF / ANIMATION
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


            # This confirmation is sent ONLY to admin.
            # User receives ONLY the actual reply.

            await message.answer(
                "✅ Sent."
            )


        except Exception as e:

            print(
                "Admin -> User error:",
                e
            )

            await message.answer(
                "❌ Failed to send reply."
            )

        return


    # ======================================
    # USER -> ADMIN
    # ======================================

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

        safe_name = html.escape(
            user.full_name
        )

        # Send user information to admin
        info = await bot.send_message(

            chat_id=ADMIN_ID,

            text=(
                "📩 <b>New User Message</b>\n\n"

                f"👤 Name: {safe_name}\n"

                f"🔹 Username: "
                f"{html.escape(username)}\n"

                f"🆔 User ID: "
                f"<code>{user.id}</code>\n\n"

                "↩️ Reply to the forwarded message."
            ),

            parse_mode="HTML"
        )


        # Forward original message to admin
        forwarded = await message.forward(
            chat_id=ADMIN_ID
        )


        # Allow admin to reply to either message
        await save_mapping(
            info.message_id,
            user.id
        )

        await save_mapping(
            forwarded.message_id,
            user.id
        )


    except Exception as e:

        print(
            "User -> Admin error:",
            e
        )


# ==========================================
# HEALTH CHECK
# ==========================================

async def health_check(request):

    return web.Response(
        text="Telegram bot is running."
    )


# ==========================================
# WEBHOOK SETUP
# ==========================================

async def set_webhook():

    try:

        await bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET
        )

        print(
            "Webhook set successfully:",
            WEBHOOK_URL
        )

    except Exception as e:

        print(
            "Webhook setup error:",
            e
        )


# ==========================================
# STARTUP
# ==========================================

async def on_startup():

    await init_db()

    # IMPORTANT:
    # Do NOT wait for Telegram webhook setup here.
    # Start it in background so Render can bind the port first.

    asyncio.create_task(
        set_webhook()
    )


# ==========================================
# WEB APP
# ==========================================

app = web.Application()


# Health check
app.router.add_get(
    "/",
    health_check
)


# Telegram webhook
webhook_handler = SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
    secret_token=WEBHOOK_SECRET
)

webhook_handler.register(
    app,
    path=WEBHOOK_PATH
)


# Connect aiogram with aiohttp
setup_application(
    app,
    dp,
    bot=bot
)


# Our startup function
app.on_startup.append(
    on_startup
)


# ==========================================
# RUN
# ==========================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))

    print(f"Starting web server on 0.0.0.0:{port}")

    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
        access_log=None
    )
