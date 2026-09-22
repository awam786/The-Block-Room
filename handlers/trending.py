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


# ============================================================
# CONVERSATION STATES
# ============================================================

SELECT_CHAIN, ENTER_CONTRACT = range(2)


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

    # Clear any previous trending session
    context.user_data.pop("trend_chain", None)
    context.user_data.pop("trend_chain_name", None)
    context.user_data.pop("trend_contract", None)
    context.user_data.pop("token_info", None)

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
        "Select the blockchain/network where your token is deployed:",
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

    # Cancel
    if data == "trend_cancel":
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Trending request cancelled."
        )

        return ConversationHandler.END

    # Extract chain
    chain_key = data.replace(
        "trend_chain_",
        "",
    )

    if chain_key not in CHAINS:
        await query.edit_message_text(
            "❌ Invalid chain selection."
        )

        return ConversationHandler.END

    # Save chain information
    context.user_data["trend_chain"] = chain_key
    context.user_data["trend_chain_name"] = CHAINS[chain_key]

    await query.edit_message_text(
        f"✅ *{CHAINS[chain_key]} selected.*\n\n"
        "📋 Send the token contract address.\n\n"
        "Send only the contract address.",
        parse_mode="Markdown",
    )

    return ENTER_CONTRACT


# ============================================================
# CONTRACT ADDRESS
# ============================================================

async def contract_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    contract = (
        update.message.text or ""
    ).strip()

    # Empty message
    if not contract:
        await update.message.reply_text(
            "❌ Please send a contract address."
        )

        return ENTER_CONTRACT

    # Basic length protection
    if len(contract) > 200:
        await update.message.reply_text(
            "❌ That contract address is too long.\n\n"
            "Please send only the contract address."
        )

        return ENTER_CONTRACT

    chain = context.user_data.get(
        "trend_chain"
    )

    chain_name = context.user_data.get(
        "trend_chain_name"
    )

    # Session expired
    if not chain:
        await update.message.reply_text(
            "❌ Your trending session has expired.\n\n"
            "Please use /trend again."
        )

        return ConversationHandler.END

    # Checking message
    checking_message = await update.message.reply_text(
        "🔎 *Checking token...*\n\n"
        "⏳ Please wait.",
        parse_mode="Markdown",
    )

    # ========================================================
    # REAL TOKEN VALIDATION
    # ========================================================

    result = await validate_token(
        chain,
        contract,
    )

    # ========================================================
    # INVALID TOKEN
    # ========================================================

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

    # ========================================================
    # SAVE TOKEN INFORMATION
    # ========================================================

    context.user_data["trend_contract"] = contract
    context.user_data["token_info"] = result

    token_name = (
        result.get("name")
        or "Unknown"
    )

    token_symbol = (
        result.get("symbol")
        or "Unknown"
    )

    launched = result.get(
        "launched",
        False,
    )

    pair = result.get(
        "pair"
    ) or {}

    # ========================================================
    # LIVE / LAUNCHED TOKEN
    # ========================================================

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

        pair_url = pair.get(
            "url"
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
        )

        if pair_url:
            message += (
                f"🔗 [View Market Pair]({pair_url})\n\n"
            )

        message += (
            "🟢 *Trading is live.*\n\n"
            "Next we'll show the available trending "
            "durations."
        )

        await checking_message.edit_text(
            message,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    # ========================================================
    # PRE-LAUNCH TOKEN
    # ========================================================

    else:

        message = (
            "✅ *TOKEN FOUND — PRE-LAUNCH*\n\n"
            f"🌐 Network: {chain_name}\n"
            f"🪙 Name: {token_name}\n"
            f"🔤 Symbol: {token_symbol}\n\n"
            "🟡 No live DEX trading pair was found yet.\n\n"
            "You can reserve a trending position before "
            "launch. We'll monitor the token and activate "
            "the trend automatically when trading becomes live.\n\n"
            "Next we'll show the available trending "
            "durations."
        )

        await checking_message.edit_text(
            message,
            parse_mode="Markdown",
        )

    return ConversationHandler.END


# ============================================================
# CANCEL COMMAND
# ============================================================

async def trend_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop(
        "trend_chain",
        None,
    )

    context.user_data.pop(
        "trend_chain_name",
        None,
    )

    context.user_data.pop(
        "trend_contract",
        None,
    )

    context.user_data.pop(
        "token_info",
        None,
    )

    await update.message.reply_text(
        "❌ Trending request cancelled."
    )

    return ConversationHandler.END
