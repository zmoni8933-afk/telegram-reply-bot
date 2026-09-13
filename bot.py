import os
import html
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
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not ADMIN_ID_RAW:
    raise RuntimeError("ADMIN_ID is missing")

if not WEBHOOK_BASE_URL:
    raise RuntimeError("WEBHOOK_BASE_URL is missing")

if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is missing")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError("ADMIN_ID must be a number")


# ==========================================
# SETTINGS
# ==========================================

DB_NAME = "bot.db"

WEBHOOK_PATH = "/telegram-webhook"


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

        # Users
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Message -> User mapping
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_map (
                admin_message_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL
            )
        """)

        # Removed/blocked users
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
        return

    async with aiosqlite.connect(DB_NAME) as db:

        # If manually removed, don't automatically add again
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

        # Remove from blocked list
        await db.execute("""
            DELETE FROM blocked_users
            WHERE user_id = ?
        """, (user_id,))

        # Add user if not already there
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

        # Remove from users
        await db.execute("""
            DELETE FROM users
            WHERE user_id = ?
        """, (user_id,))

        # Block the user
        await db.execute("""
            INSERT OR IGNORE INTO blocked_users (
                user_id
            )
            VALUES (?)
        """, (user_id,))

        # Remove message mappings
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
# ADMIN COMMANDS
# ==========================================

@dp.message()
async def message_handler(message: Message):

    if not message.from_user:
        return

    user_id = message.from_user.id


    # ======================================
    # ADMIN
    # ======================================

    if user_id == ADMIN_ID:

        text = message.text or ""


        # ----------------------------------
        # /adduser
        # ----------------------------------

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
                f"✅ User added.\n\n"
                f"🆔 ID: <code>{target_id}</code>",
                parse_mode="HTML"
            )

            return


        # ----------------------------------
        # /removeuser
        # ----------------------------------

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
                f"🗑️ User removed and blocked.\n\n"
                f"🆔 ID: <code>{target_id}</code>",
                parse_mode="HTML"
            )

            return


        # ----------------------------------
        # /users
        # ----------------------------------

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

            for number, user in enumerate(users, start=1):

                uid = user[0]
                name = user[1] or "Unknown"
                username = user[2]

                if username:
                    username_text = f"@{username}"
                else:
                    username_text = "No username"

                lines.append(
                    f"{number}. "
                    f"👤 {html.escape(name)}\n"
                    f"   🔹 {username_text}\n"
                    f"   🆔 <code>{uid}</code>\n"
                )

            result = "\n".join(lines)

            # Telegram message limit protection
            if len(result) > 4000:

                result = result[:4000] + "\n\n⚠️ List truncated."

            await message.answer(
                result,
                parse_mode="HTML"
            )

            return


        # ----------------------------------
        # ADMIN REPLY -> USER
        # ----------------------------------

        if not message.reply_to_message:

            await message.answer(
                "↩️ Reply to a user's forwarded message.\n\n"
                "Admin commands:\n"
                "/adduser USER_ID\n"
                "/removeuser USER_ID\n"
                "/users"
            )

            return


        replied_message_id = (
            message.reply_to_message.message_id
        )

        target_user_id = await get_user_from_message(
            replied_message_id
        )

        if not target_user_id:

            await message.answer(
                "❌ User mapping not found."
            )

            return


        # ----------------------------------
        # SEND REPLY
        # ----------------------------------

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


    # ======================================
    # USER -> ADMIN
    # ======================================

    try:

        allowed = await save_user(message)

        # Removed/blocked user
        if allowed is False:
            return

        user = message.from_user

        username = (
            f"@{user.username}"
            if user.username
            else "No username"
        )

        # Escape HTML
        safe_name = html.escape(
            user.full_name
        )

        # User information message
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


        # Forward original message
        forwarded = await message.forward(
            chat_id=ADMIN_ID
        )


        # Save mappings
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
# WEBHOOK STARTUP
# ==========================================

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


# ==========================================
# WEB APP
# ==========================================

app = web.Application()


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


dp.startup.register(
    on_startup
)


# ==========================================
# START SERVER
# ==========================================

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
