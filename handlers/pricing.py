from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.ext import ContextTypes

from database.connection import get_pool
from handlers.admin import admin_only


PRICING_COMMANDS = {
    "2hour": 2,
    "6hours": 6,
    "12hours": 12,
    "24hours": 24,
}


async def set_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await admin_only(update, context):
        return

    command = update.message.text.split()[0]
    command = command.split("@")[0].lstrip("/").lower()

    duration = PRICING_COMMANDS.get(command)

    if duration is None:
        await update.message.reply_text("❌ Invalid pricing command.")
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            f"Usage:\n/{command} <USDT amount>\n\n"
            f"Example:\n/{command} 110"
        )
        return

    try:
        price = Decimal(context.args[0])
    except InvalidOperation:
        await update.message.reply_text(
            "❌ Please enter a valid USDT amount."
        )
        return

    if price <= 0:
        await update.message.reply_text(
            "❌ Price must be greater than 0."
        )
        return

    if price > Decimal("1000000"):
        await update.message.reply_text(
            "❌ Price is too large."
        )
        return

    pool = get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pricing (duration_hours, price_usdt)
            VALUES ($1, $2)
            ON CONFLICT (duration_hours)
            DO UPDATE SET price_usdt = EXCLUDED.price_usdt
            """,
            duration,
            price,
        )

    await update.message.reply_text(
        f"✅ Pricing updated.\n\n"
        f"Duration: {duration} hour(s)\n"
        f"Price: {price} USDT"
    )


async def show_prices(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT duration_hours, price_usdt
            FROM pricing
            ORDER BY duration_hours
            """
        )

    text = "💰 THE BLOCK ROOM — CURRENT PRICES\n\n"

    for row in rows:
        text += (
            f"⏱ {row['duration_hours']}h — "
            f"{row['price_usdt']} USDT\n"
        )

    await update.message.reply_text(text)
