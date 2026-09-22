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
from services.orders import (
    create_order,
    get_payment_wallet,
    submit_transaction_hash,
)
from database.connection import get_pool


# ============================================================
# CONVERSATION STATES
# ============================================================

(
    SELECT_CHAIN,
    ENTER_CONTRACT,
    SELECT_DURATION,
    ENTER_TX_HASH,
) = range(4)


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
# START
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
# CHAIN SELECTED
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
# CONTRACT RECEIVED
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
            "reason": (
                "Token verification service is "
                "temporarily unavailable."
            ),
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

    token_name = result.get(
        "name"
    ) or "Unknown"

    token_symbol = result.get(
        "symbol"
    ) or "Unknown"

    launched = result.get(
        "launched",
        False,
    )

    pair = result.get(
        "pair"
    ) or {}

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
            "The system will monitor the token to go live.",
            parse_mode="Markdown",
        )

    await send_duration_options(
        update,
        context,
    )

    return SELECT_DURATION


# ============================================================
# SHOW PACKAGES
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
            "❌ No trending packages are currently available."
        )

        return

    buttons = []

    for row in rows:
        hours = row["duration_hours"]

        price = Decimal(
            str(row["price_usdt"])
        )

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
        "Select how long you want your token to "
        "remain on The Block Room trending:",
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

    if not data.startswith(
        "trend_duration_"
    ):
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

    chain = context.user_data.get(
        "trend_chain"
    )

    contract = context.user_data.get(
        "trend_contract"
    )

    token_info = context.user_data.get(
        "token_info",
        {},
    )

    if not chain or not contract:
        await query.edit_message_text(
            "❌ Your trending session has expired.\n\n"
            "Please use /trend again."
        )

        return ConversationHandler.END

    token_name = token_info.get(
        "name"
    ) or "Unknown"

    token_symbol = token_info.get(
        "symbol"
    ) or "Unknown"

    launched = token_info.get(
        "launched",
        False,
    )

    # --------------------------------------------------------
    # Check payment wallet
    # --------------------------------------------------------

    payment_chain_map = {
        "bnb": "BNB",
        "ethereum": "ETH",
        "solana": "SOL",
    }

    payment_chain = payment_chain_map.get(
        chain
    )

    if not payment_chain:

        await query.edit_message_text(
            "❌ Payment for this network is not "
            "configured yet.\n\n"
            "Please contact support."
        )

        return ConversationHandler.END

    wallet = await get_payment_wallet(
        payment_chain
    )

    if not wallet:

        await query.edit_message_text(
            "⚠️ *PAYMENT TEMPORARILY UNAVAILABLE*\n\n"
            f"The {payment_chain} USDT payment wallet "
            "has not been configured yet.\n\n"
            "Please contact support.",
            parse_mode="Markdown",
        )

        return ConversationHandler.END

    # --------------------------------------------------------
    # Create order
    # --------------------------------------------------------

    try:

        order = await create_order(
            telegram_id=update.effective_user.id,
            chain=chain,
            contract_address=contract,
            token_name=token_name,
            token_symbol=token_symbol,
            duration_hours=duration,
            amount_usdt=price,
            waiting_for_launch=not launched,
        )

    except Exception as exc:

        print(
            f"Order creation error: {exc}"
        )

        await query.edit_message_text(
            "❌ We couldn't create your order.\n\n"
            "Please try again or contact support."
        )

        return ConversationHandler.END

    context.user_data["order_id"] = order["id"]
    context.user_data["order_number"] = order[
        "order_number"
    ]

    # --------------------------------------------------------
    # Payment instructions
    # --------------------------------------------------------

    await query.edit_message_text(
        "💳 *PAYMENT REQUIRED*\n\n"
        f"🧾 Order: `{order['order_number']}`\n"
        f"🪙 Token: {token_name} ({token_symbol})\n"
        f"🌐 Token Network: {CHAINS[chain]}\n"
        f"⏱ Duration: {duration} hours\n\n"
        f"💰 *Send exactly {price:g} USDT*\n\n"
        f"🔗 Payment Network:\n"
        f"{wallet['chain']} — {order['payment_network']}\n\n"
        f"📥 *Payment Address:*\n"
        f"`{wallet['address']}`\n\n"
        "⚠️ Send USDT only using the network shown above.\n"
        "⚠️ Sending another token or using another network "
        "may result in loss of funds.\n\n"
        "After sending the payment, press the button below "
        "and submit your transaction hash.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ I Have Paid",
                        callback_data="trend_paid",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel Order",
                        callback_data="trend_cancel_payment",
                    )
                ],
            ]
        ),
        parse_mode="Markdown",
    )

    return ENTER_TX_HASH


# ============================================================
# PAYMENT BUTTON
# ============================================================

async def payment_started(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    order_number = context.user_data.get(
        "order_number"
    )

    if not order_number:
        await query.edit_message_text(
            "❌ Your order session has expired.\n\n"
            "Please use /trend again."
        )

        return ConversationHandler.END

    await query.edit_message_text(
        "🔐 *SUBMIT TRANSACTION HASH*\n\n"
        f"🧾 Order: `{order_number}`\n\n"
        "Please send the transaction hash / TXID of "
        "your USDT payment.\n\n"
        "Send only the transaction hash.",
        parse_mode="Markdown",
    )

    return ENTER_TX_HASH


# ============================================================
# TX HASH RECEIVED
# ============================================================

async def transaction_hash_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    tx_hash = (
        update.message.text or ""
    ).strip()

    if not tx_hash:

        await update.message.reply_text(
            "❌ Please send your transaction hash."
        )

        return ENTER_TX_HASH

    if len(tx_hash) > 300:

        await update.message.reply_text(
            "❌ That transaction hash is too long.\n\n"
            "Please send only the TX hash."
        )

        return ENTER_TX_HASH

    order_id = context.user_data.get(
        "order_id"
    )

    order_number = context.user_data.get(
        "order_number"
    )

    if not order_id or not order_number:

        await update.message.reply_text(
            "❌ Your order session has expired.\n\n"
            "Please use /trend again."
        )

        return ConversationHandler.END

    result = await submit_transaction_hash(
        order_id,
        tx_hash,
    )

    if not result.get("success"):

        reason = result.get(
            "reason"
        )

        if reason == "TX_HASH_ALREADY_USED":

            await update.message.reply_text(
                "❌ *TRANSACTION ALREADY SUBMITTED*\n\n"
                f"This TX hash is already connected to order "
                f"`{result.get('order_number')}`.\n\n"
                "Please check your transaction hash.",
                parse_mode="Markdown",
            )

            return ENTER_TX_HASH

        if reason == "ORDER_NOT_FOUND":

            await update.message.reply_text(
                "❌ Order not found.\n\n"
                "Please start again with /trend."
            )

            return ConversationHandler.END

        await update.message.reply_text(
            "❌ This order is no longer accepting payment "
            "submissions."
        )

        return ConversationHandler.END

    # --------------------------------------------------------
    # Payment is NOT verified yet.
    # --------------------------------------------------------

    await update.message.reply_text(
        "⏳ *PAYMENT SUBMITTED*\n\n"
        f"🧾 Order: `{order_number}`\n\n"
        "Your transaction hash has been received.\n\n"
        "🔎 We will now verify the transaction on the "
        "blockchain.\n\n"
        "Your order will only become active after the "
        "payment passes all verification checks.",
        parse_mode="Markdown",
    )

    context.user_data.clear()

    return ConversationHandler.END


# ============================================================
# CANCEL PAYMENT
# ============================================================

async def cancel_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    order_id = context.user_data.get(
        "order_id"
    )

    if order_id:

        pool = get_pool()

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE orders
                SET
                    status = 'CANCELLED',
                    updated_at = NOW()
                WHERE id = $1
                  AND status = 'PENDING_PAYMENT'
                """,
                order_id,
            )

    context.user_data.clear()

    await query.edit_message_text(
        "❌ Order cancelled."
    )

    return ConversationHandler.END


# ============================================================
# GENERAL CANCEL
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
