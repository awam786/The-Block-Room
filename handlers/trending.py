from decimal import Decimal

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)

from handlers.start import register_user
from services.token_validator import validate_token
from database.connection import get_pool


# ============================================================
# CONVERSATION STATES
# ============================================================

SELECT_CHAIN, ENTER_CONTRACT, SELECT_DURATION = range(3)


# ============================================================
# SUPPORTED CHAINS
# ============================================================

CHAINS = {
    "bnb": "BNB Smart Chain",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "robinhood": "Robinhood",
}


# ============================================================
# START TRENDING
# ============================================================

async def trend_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await register_user(update)

    context.user_data.clear()

    keyboard = [
        [
            InlineKeyboardButton(
                "🟡 BNB Smart Chain",
                callback_data="trend_chain_bnb",
            )
        ],
        [
            InlineKeyboardButton(
                "🔷 Ethereum",
                callback_data="trend_chain_ethereum",
            )
        ],
        [
            InlineKeyboardButton(
                "🟣 Solana",
                callback_data="trend_chain_solana",
            )
        ],
        [
            InlineKeyboardButton(
                "🟠 Robinhood",
                callback_data="trend_chain_robinhood",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="trend_cancel",
            )
        ],
    ]

    await update.message.reply_text(
        "📈 *LIST ON TRENDING*\n\n"
        "Select the blockchain/network where your token "
        "is deployed:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

    return SELECT_CHAIN


# ============================================================
# CHAIN SELECTION
# ============================================================

async def chain_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "trend_cancel":
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Trending request cancelled."
        )

        return ConversationHandler.END

    chain_key = data.replace(
        "trend_chain_",
        "",
    )

    if chain_key not in CHAINS:
        await query.edit_message_text(
            "❌ Invalid chain selection."
        )

        return ConversationHandler.END

    context.user_data["trend_chain"] = chain_key
    context.user_data["trend_chain_name"] = CHAINS[
        chain_key
    ]

    await query.edit_message_text(
        f"✅ *{CHAINS[chain_key]} selected.*\n\n"
        "📋 Send the token contract address.\n\n"
        "Send only the contract address.",
        parse_mode="Markdown",
    )

    return ENTER_CONTRACT


# ============================================================
# CONTRACT VALIDATION
# ============================================================

async def contract_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    contract = (
        update.message.text or ""
    ).strip()

    if not contract:
        await update.message.reply_text(
            "❌ Please send a contract address."
        )

        return ENTER_CONTRACT

    if len(contract) > 200:
        await update.message.reply_text(
            "❌ That contract address is too long."
        )

        return ENTER_CONTRACT

    chain = context.user_data.get(
        "trend_chain"
    )

    chain_name = context.user_data.get(
        "trend_chain_name"
    )

    if not chain:
        await update.message.reply_text(
            "❌ Your trending session has expired.\n\n"
            "Please use /trend again."
        )

        return ConversationHandler.END

    checking_message = await update.message.reply_text(
        "🔎 *Checking token...*\n\n"
        "⏳ Please wait.",
        parse_mode="Markdown",
    )

    try:
        result = await validate_token(
            chain,
            contract,
        )
    except Exception:
        result = {
            "valid": False,
            "reason": "Token verification service is temporarily unavailable.",
        }

    if not result.get("valid"):
        reason = result.get(
            "reason",
            "The token could not be verified.",
        )

        await checking_message.edit_text(
            "❌ *TOKEN VERIFICATION FAILED*\n\n"
            f"🌐 Network: {chain_name}\n"
            f"📋 Contract:\n`{contract}`\n\n"
            f"Reason:\n{reason}\n\n"
            "Please check the network and contract address "
            "and try again.",
            parse_mode="Markdown",
        )

        return ENTER_CONTRACT

    context.user_data["trend_contract"] = contract
    context.user_data["token_info"] = result

    token_name = result.get("name") or "Unknown"
    token_symbol = result.get("symbol") or "Unknown"

    launched = result.get(
        "launched",
        False,
    )

    pair = result.get("pair") or {}

    if launched:
        liquidity = pair.get(
            "liquidity_usd",
            0,
        ) or 0

        volume_24h = pair.get(
            "volume_24h",
            0,
        ) or 0

        market_cap = pair.get(
            "market_cap",
            0,
        ) or 0

        price_change = pair.get(
            "price_change_24h",
            0,
        ) or 0

        dex = pair.get(
            "dex",
            "Unknown",
        )

        message = (
            "✅ *TOKEN VERIFIED*\n\n"
            f"🌐 Network: {chain_name}\n"
            f"🪙 Name: {token_name}\n"
            f"🔤 Symbol: {token_symbol}\n\n"
            f"💧 Liquidity: ${float(liquidity):,.2f}\n"
            f"📊 24h Volume: ${float(volume_24h):,.2f}\n"
            f"💎 Market Cap: ${float(market_cap):,.2f}\n"
            f"📈 24h Change: {float(price_change):+.2f}%\n"
            f"🔄 DEX: {dex}\n\n"
            "🟢 *Trading is live.*"
        )

        await checking_message.edit_text(
            message,
            parse_mode="Markdown",
        )

    else:
        await checking_message.edit_text(
            "✅ *TOKEN FOUND — PRE-LAUNCH*\n\n"
            f"🌐 Network: {chain_name}\n"
            f"🪙 Name: {token_name}\n"
            f"🔤 Symbol: {token_symbol}\n\n"
            "🟡 No live DEX trading pair was found yet.\n\n"
            "You can reserve trending before launch. "
            "The system will monitor for the token to go live.",
            parse_mode="Markdown",
        )

    # Show duration options
    await send_duration_options(
        update,
        context,
    )

    return SELECT_DURATION


# ============================================================
# DURATION OPTIONS
# ============================================================

async def send_duration_options(
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

    if not rows:
        await update.message.reply_text(
            "❌ No trending packages are currently available.\n\n"
            "Please contact support."
        )

        return

    buttons = []

    for row in rows:
        hours = row["duration_hours"]
        price = Decimal(str(row["price_usdt"]))

        buttons.append(
            [
                InlineKeyboardButton(
                    f"⏱ {hours}h — {price:g} USDT",
                    callback_data=f"trend_duration_{hours}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="trend_cancel_duration",
            )
        ]
    )

    await update.message.reply_text(
        "💰 *CHOOSE YOUR TRENDING PACKAGE*\n\n"
        "Select how long you want your token to remain "
        "on The Block Room trending:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


# ============================================================
# DURATION SELECTED
# ============================================================

async def duration_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "trend_cancel_duration":
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Trending request cancelled."
        )

        return ConversationHandler.END

    if not data.startswith("trend_duration_"):
        await query.edit_message_text(
            "❌ Invalid duration selection."
        )

        return SELECT_DURATION

    try:
        duration = int(
            data.replace(
                "trend_duration_",
                "",
            )
        )
    except ValueError:
        await query.edit_message_text(
            "❌ Invalid duration."
        )

        return SELECT_DURATION

    pool = get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT duration_hours, price_usdt
            FROM pricing
            WHERE duration_hours = $1
            """,
            duration,
        )

    if not row:
        await query.edit_message_text(
            "❌ This package is no longer available.\n\n"
            "Please start again with /trend."
        )

        return ConversationHandler.END

    price = Decimal(
        str(row["price_usdt"])
    )

    context.user_data["trend_duration"] = duration
    context.user_data["trend_price"] = price

    token_info = context.user_data.get(
        "token_info",
        {},
    )

    token_name = token_info.get(
        "name",
        "Unknown",
    )

    token_symbol = token_info.get(
        "symbol",
        "Unknown",
    )

    chain_name = context.user_data.get(
        "trend_chain_name",
        "Unknown",
    )

    await query.edit_message_text(
        "🧾 *TRENDING ORDER*\n\n"
        f"🪙 Token: {token_name} ({token_symbol})\n"
        f"🌐 Network: {chain_name}\n"
        f"⏱ Duration: {duration} hours\n"
        f"💰 Price: {price:g} USDT\n\n"
        "Your order information has been prepared.\n\n"
        "💳 The next step is payment.",
        parse_mode="Markdown",
    )

    return ConversationHandler.END


# ============================================================
# CANCEL
# ============================================================

async def trend_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Trending request cancelled."
    )

    return ConversationHandler.END
