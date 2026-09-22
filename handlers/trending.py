from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)

from handlers.start import register_user


# Conversation states
SELECT_CHAIN, ENTER_CONTRACT = range(2)


CHAINS = {
    "bnb": "BNB Smart Chain",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "robinhood": "Robinhood",
}


async def trend_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await register_user(update)

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


async def chain_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "trend_cancel":
        await query.edit_message_text(
            "❌ Trending request cancelled."
        )
        return ConversationHandler.END

    chain_key = data.replace("trend_chain_", "")

    if chain_key not in CHAINS:
        await query.edit_message_text(
            "❌ Invalid chain selection."
        )
        return ConversationHandler.END

    context.user_data["trend_chain"] = chain_key
    context.user_data["trend_chain_name"] = CHAINS[chain_key]

    await query.edit_message_text(
        f"✅ Selected: *{CHAINS[chain_key]}*\n\n"
        "📋 Now send the token's contract address.\n\n"
        "Send only the contract address.",
        parse_mode="Markdown",
    )

    return ENTER_CONTRACT


async def contract_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    contract = (update.message.text or "").strip()

    if not contract:
        await update.message.reply_text(
            "❌ Please send a contract address."
        )
        return ENTER_CONTRACT

    if len(contract) > 200:
        await update.message.reply_text(
            "❌ That contract address is too long.\n\n"
            "Please send only the contract address."
        )
        return ENTER_CONTRACT

    chain = context.user_data.get("trend_chain")
    chain_name = context.user_data.get("trend_chain_name")

    if not chain:
        await update.message.reply_text(
            "❌ Your trending session expired.\n\n"
            "Please use /trend again."
        )
        return ConversationHandler.END

    # Store the address temporarily.
    # Actual blockchain/API validation will happen in the
    # service layer that we build next.
    context.user_data["trend_contract"] = contract

    await update.message.reply_text(
        "🔎 *Checking token...*\n\n"
        f"Network: {chain_name}\n"
        f"Contract: `{contract}`\n\n"
        "⏳ Token verification will be connected next.",
        parse_mode="Markdown",
    )

    return ConversationHandler.END


async def trend_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("trend_chain", None)
    context.user_data.pop("trend_chain_name", None)
    context.user_data.pop("trend_contract", None)

    await update.message.reply_text(
        "❌ Trending request cancelled."
    )

    return ConversationHandler.END
