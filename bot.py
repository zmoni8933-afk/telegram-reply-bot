import os
import aiosqlite

from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)


# =========================
# ENVIRONMENT VARIABLES
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is missing")

if not WEBHOOK_BASE_URL:
    raise RuntimeError("WEBHOOK_BASE_URL is missing")

if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is missing")


# =========================
# SETTINGS
# =========================

DB_NAME = "bot.db"

WEBHOOK_PATH = "/telegram-webhook"


# =========================
# BOT / DISPATCHER
# =========================

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
                username TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_map (
                admin_message_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL
            )
        """)

        await db.commit()


async def save_user(message: Message):

    user = message.from_user

    async with aiosqlite.connect(DB_NAME) as db:

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


async def save_mapping(admin_message_id: int, user_id: int):

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


async def get_user(admin_message_id: int):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT user_id
            FROM message_map
            WHERE admin_message_id = ?
        """, (admin_message_id,))

        row = await cursor.fetchone()

        return row[0] if row else None


# =========================
# MESSAGE HANDLER
# =========================

@dp.message()
async def message_handler(message: Message):

    # =================================
    # ADMIN -> USER
    # =================================

    if message.from_user.id == ADMIN_ID:

        if not message.reply_to_message:

            await message.answer(
                "↩️ Reply to a user's forwarded message."
            )

            return

        replied_message_id = (
            message.reply_to_message.message_id
        )

        user_id = await get_user(
            replied_message_id
        )

        if not user_id:

            await message.answer(
                "❌ User mapping not found."
            )

            return

        try:

            # TEXT
            if message.text:

                await bot.send_message(
                    chat_id=user_id,
                    text=message.text
                )

            # PHOTO
            elif message.photo:

                await bot.send_photo(
                    chat_id=user_id,
                    photo=message.photo[-1].file_id,
                    caption=message.caption
                )

            # VIDEO
            elif message.video:

                await bot.send_video(
                    chat_id=user_id,
                    video=message.video.file_id,
                    caption=message.caption
                )

            # DOCUMENT
            elif message.document:

                await bot.send_document(
                    chat_id=user_id,
                    document=message.document.file_id,
                    caption=message.caption
                )

            # VOICE
            elif message.voice:

                await bot.send_voice(
                    chat_id=user_id,
                    voice=message.voice.file_id,
                    caption=message.caption
                )

            # AUDIO
            elif message.audio:

                await bot.send_audio(
                    chat_id=user_id,
                    audio=message.audio.file_id,
                    caption=message.caption
                )

            # STICKER
            elif message.sticker:

                await bot.send_sticker(
                    chat_id=user_id,
                    sticker=message.sticker.file_id
                )

            # ANIMATION / GIF
            elif message.animation:

                await bot.send_animation(
                    chat_id=user_id,
                    animation=message.animation.file_id,
                    caption=message.caption
                )

            else:

                await message.answer(
                    "⚠️ This message type is not supported."
                )

                return

            await message.answer(
                "✅ Reply sent."
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


    # =================================
    # USER -> ADMIN
    # =================================

    try:

        await save_user(message)

        user = message.from_user

        username = (
            f"@{user.username}"
            if user.username
            else "No username"
        )

        # User information
        info = await bot.send_message(

            chat_id=ADMIN_ID,

            text=(
                "📩 <b>New User Message</b>\n\n"

                f"👤 Name: {user.full_name}\n"

                f"🔹 Username: {username}\n"

                f"🆔 User ID: "
                f"<code>{user.id}</code>\n\n"

                "↩️ Reply to the forwarded message."
            ),

            parse_mode="HTML"
        )


        # Forward original message
        forwarded = await message.forward(
            chat_id=ADMIN_ID
        )


        # Save both message IDs
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


# =========================
# WEBHOOK STARTUP
# =========================

async def on_startup(bot: Bot):

    await init_db()

    webhook_url = (
        f"{WEBHOOK_BASE_URL}"
        f"{WEBHOOK_PATH}"
    )

    await bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET
    )

    print(
        "Webhook set:",
        webhook_url
    )


# =========================
# WEB APP
# =========================

app = web.Application()


# Telegram webhook handler
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


# Startup hook
dp.startup.register(
    on_startup
)


# =========================
# RENDER SERVER
# =========================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    web.run_app(
        app,
        host="0.0.0.0",
        port=port
    )
