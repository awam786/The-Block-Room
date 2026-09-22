from telegram import Update
from telegram.ext import ContextTypes

from database.connection import get_pool
from handlers.admin import admin_only


WALLET_COMMANDS = {
    "bnb": ("BNB", "USDT BEP-20"),
    "eth": ("ETH", "USDT ERC-20"),
    "sol": ("SOL", "USDT SPL"),
}


async def set_wallet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await admin_only(update, context):
        return

    command = update.message.text.split()[0]
    command = command.split("@")[0].lstrip("/").lower()

    wallet_info = WALLET_COMMANDS.get(command)

    if wallet_info is None:
        await update.message.reply_text("❌ Invalid wallet command.")
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            f"Usage:\n/{command.upper()} <wallet address>"
        )
        return

    address = context.args[0].strip()

    if len(address) < 20:
        await update.message.reply_text(
            "❌ That doesn't look like a valid wallet address."
        )
        return

    chain, token_type = wallet_info

    pool = get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO wallets
                (chain, address, token_symbol, is_active, updated_at)
            VALUES
                ($1, $2, 'USDT', TRUE, NOW())
            ON CONFLICT (chain)
            DO UPDATE SET
                address = EXCLUDED.address,
                is_active = TRUE,
                updated_at = NOW()
            """,
            chain,
            address,
        )

    await update.message.reply_text(
        f"✅ {token_type} wallet updated.\n\n"
        f"Chain: {chain}\n"
        f"Address:\n`{address}`",
        parse_mode="Markdown",
    )


async def remove_wallet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await admin_only(update, context):
        return

    command = update.message.text.split()[0]
    command = command.split("@")[0].lstrip("/").lower()

    remove_commands = {
        "removebnb": "BNB",
        "removeeth": "ETH",
        "removesol": "SOL",
    }

    chain = remove_commands.get(command)

    if not chain:
        await update.message.reply_text(
            "❌ Invalid wallet removal command."
        )
        return

    pool = get_pool()

    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE wallets
            SET is_active = FALSE,
                updated_at = NOW()
            WHERE chain = $1
            """,
            chain,
        )

    if result == "UPDATE 0":
        await update.message.reply_text(
            f"ℹ️ No {chain} wallet is currently configured."
        )
        return

    await update.message.reply_text(
        f"✅ {chain} USDT payment wallet disabled."
    )


async def show_wallets(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await admin_only(update, context):
        return

    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chain, address, is_active
            FROM wallets
            ORDER BY chain
            """
        )

    if not rows:
        await update.message.reply_text(
            "💳 No payment wallets configured."
        )
        return

    text = "💳 USDT PAYMENT WALLETS\n\n"

    for row in rows:
        status = "🟢 Active" if row["is_active"] else "🔴 Disabled"

        text += (
            f"{row['chain']} — {status}\n"
            f"{row['address']}\n\n"
        )

    await update.message.reply_text(text)
