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


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

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


# =========================================================
# SETTINGS
# =========================================================

DB_NAME = "bot.db"

WEBHOOK_PATH = "/telegram-webhook"

WEBHOOK_URL = (
    RENDER_EXTERNAL_URL.rstrip("/")
    + WEBHOOK_PATH
)


# =========================================================
# BOT
# =========================================================

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()


# =========================================================
# DATABASE
# =========================================================

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


# =========================================================
# CHECK USER
# =========================================================

async def is_blocked(user_id: int):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT user_id
            FROM blocked_users
            WHERE user_id = ?
        """, (user_id,))

        row = await cursor.fetchone()

        return row is not None


# =========================================================
# SAVE USER
# =========================================================

async def save_user(message: Message):

    user = message.from_user

    if not user:
        return False

    blocked = await is_blocked(user.id)

    if blocked:
        return False

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

    return True


# =========================================================
# MANUALLY ADD USER
# =========================================================

async def manual_add_user(user_id: int):

    async with aiosqlite.connect(DB_NAME) as db:

        # Remove from blocked list
        await db.execute("""
            DELETE FROM blocked_users
            WHERE user_id = ?
        """, (user_id,))

        # Add user
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


# =========================================================
# MANUALLY REMOVE USER
# =========================================================

async def manual_remove_user(user_id: int):

    async with aiosqlite.connect(DB_NAME) as db:

        # Remove from users
        await db.execute("""
            DELETE FROM users
            WHERE user_id = ?
        """, (user_id,))

        # Block user
        await db.execute("""
            INSERT OR IGNORE INTO blocked_users (
                user_id
            )
            VALUES (?)
        """, (user_id,))

        # Remove old mappings
        await db.execute("""
            DELETE FROM message_map
            WHERE user_id = ?
        """, (user_id,))

        await db.commit()


# =========================================================
# GET USERS
# =========================================================

async def get_users():

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT
                user_id,
                name,
                username
            FROM users
            ORDER BY added_at DESC
        """)

        return await cursor.fetchall()


# =========================================================
# SAVE MESSAGE MAPPING
# =========================================================

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


# =========================================================
# GET USER FROM ADMIN MESSAGE
# =========================================================

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

        if row:
            return row[0]

        return None


# =========================================================
# MAIN MESSAGE HANDLER
# =========================================================

@dp.message()
async def message_handler(message: Message):

    if not message.from_user:
        return

    sender_id = message.from_user.id


    # =====================================================
    # ADMIN MESSAGE
    # =====================================================

    if sender_id == ADMIN_ID:

        text = message.text or ""


        # -------------------------------------------------
        # /adduser
        # -------------------------------------------------

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


        # -------------------------------------------------
        # /removeuser
        # -------------------------------------------------

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


        # -------------------------------------------------
        # /users
        # -------------------------------------------------

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


            # Telegram message limit
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


        # -------------------------------------------------
        # ADMIN REPLY TO USER
        # -------------------------------------------------

        if not message.reply_to_message:

            await message.answer(
                "↩️ Reply to a user's message.\n\n"
                "Admin commands:\n"
                "/adduser USER_ID\n"
                "/removeuser USER_ID\n"
                "/users"
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


        # -------------------------------------------------
        # SEND ADMIN REPLY TO USER
        # -------------------------------------------------

        try:

            # copy_to supports Telegram message types
            await message.copy_to(
                chat_id=target_user_id
            )


            await message.answer(
                "✅ Reply sent."
            )


        except Exception as e:

            print(
                "Admin -> User error:",
                repr(e)
            )


            await message.answer(
                "❌ Failed to send reply."
            )


        return


    # =====================================================
    # USER MESSAGE
    # =====================================================

    try:

        # Check blocked + save user
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


        # -------------------------------------------------
        # SEND USER INFO TO ADMIN
        # -------------------------------------------------

        info = await bot.send_message(

            chat_id=ADMIN_ID,

            text=(
                "📩 <b>New User Message</b>\n\n"

                f"👤 Name: "
                f"{safe_name}\n"

                f"🔹 Username: "
                f"{html.escape(username)}\n"

                f"🆔 User ID: "
                f"<code>{user.id}</code>\n\n"

                "↩️ Reply to this message "
                "or the forwarded message."
            ),

            parse_mode="HTML"
        )


        # -------------------------------------------------
        # FORWARD ORIGINAL MESSAGE TO ADMIN
        # -------------------------------------------------

        forwarded = await message.forward(
            chat_id=ADMIN_ID
        )


        # -------------------------------------------------
        # SAVE BOTH MESSAGE IDs
        # -------------------------------------------------

        await save_mapping(
            info.message_id,
            user.id
        )


        await save_mapping(
            forwarded.message_id,
            user.id
        )


        print(
            f"User message received: "
            f"{user.id} -> Admin"
        )


    except Exception as e:

        print(
            "User -> Admin error:",
            repr(e)
        )


# =========================================================
# HEALTH CHECK
# =========================================================

async def health_check(request):

    return web.Response(
        text="Telegram bot is running."
    )


# =========================================================
# WEB APP
# =========================================================

app = web.Application()


app.router.add_get(
    "/",
    health_check
)


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

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


# =========================================================
# PREPARE BOT
# =========================================================

async def prepare_bot():

    print("Initializing database...")

    await init_db()

    print(
        "Setting webhook:",
        WEBHOOK_URL
    )

    await bot.set_webhook(

        url=WEBHOOK_URL,

        secret_token=WEBHOOK_SECRET
    )

    print(
        "Webhook set:",
        WEBHOOK_URL
    )


# =========================================================
# STARTUP
# =========================================================

async def on_startup(app):

    print("Initializing database...")

    await init_db()

    print(
        "Setting webhook:",
        WEBHOOK_URL
    )

    await bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )

    print(
        "Webhook set:",
        WEBHOOK_URL
    )


# =========================================================
# SHUTDOWN
# =========================================================

async def on_shutdown(app):

    print("Shutting down bot...")

    await bot.session.close()


# =========================================================
# REGISTER STARTUP / SHUTDOWN
# =========================================================

app.on_startup.append(
    on_startup
)

app.on_cleanup.append(
    on_shutdown
)


# =========================================================
# RUN SERVER
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    print(
        f"Starting web server "
        f"on 0.0.0.0:{port}"
    )

    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
        access_log=None
    )
