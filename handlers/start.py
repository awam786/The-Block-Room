from telegram import Update
from telegram.ext import ContextTypes

from database.connection import get_pool


async def register_user(update: Update):
    user = update.effective_user

    if not user:
        return

    pool = get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (
                telegram_id,
                username,
                first_name,
                last_name
            )
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (telegram_id)
            DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                updated_at = NOW()
            """,
            user.id,
            user.username,
            user.first_name,
            user.last_name,
        )


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await register_user(update)

    user = update.effective_user

    await update.message.reply_text(
        f"👋 Welcome {user.first_name}!\n\n"
        "🏛️ *THE BLOCK ROOM*\n\n"
        "Your crypto trending platform for "
        "BNB, Ethereum, Solana and more.\n\n"
        "📈 List a token on trending with /trend\n"
        "💬 Need help? Use /support\n"
        "📋 See available commands with /help",
        parse_mode="Markdown",
    )
