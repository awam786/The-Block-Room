import re

import httpx

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
)

from database.connection import get_pool


SUPPORTED_CHAINS = {
    "bnb": "BNB Smart Chain",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "robinhood": "Robinhood",
}


# ============================================================
# CONVERSATION STATES
# ============================================================

SELECT_TOKEN_CHAIN = 0
ENTER_TOKEN_ADDRESS = 1
ENTER_BUTTON = 2
ENTER_TITLE = 3
ENTER_TEMPLATE = 4
ENTER_EMOJI = 5
ENTER_MIN_BUY = 6
ENTER_MEDIA = 7
SETTINGS = 8
REMOVE_TOKEN = 9


# ============================================================
# GENERAL HELPERS
# ============================================================

async def is_group_admin(
    update: Update,
) -> bool:
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user:
        return False

    if chat.type not in (
        "group",
        "supergroup",
    ):
        return False

    try:
        member = await chat.get_member(
            user.id
        )

        return member.status in (
            "administrator",
            "creator",
        )

    except Exception:
        return False


async def require_group_admin(
    update: Update,
) -> bool:
    if await is_group_admin(update):
        return True

    if update.callback_query:
        await update.callback_query.answer(
            "Only group administrators can use BuyBot settings.",
            show_alert=True,
        )

    elif update.message:
        await update.message.reply_text(
            "❌ Only group administrators can manage BuyBot settings."
        )

    return False


def settings_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Add Token",
                    callback_data="bb_add_token",
                ),
                InlineKeyboardButton(
                    "➖ Remove Token",
                    callback_data="bb_remove_token",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🖼 Media",
                    callback_data="bb_media",
                ),
                InlineKeyboardButton(
                    "✏️ Title",
                    callback_data="bb_title",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📝 Template",
                    callback_data="bb_template",
                ),
                InlineKeyboardButton(
                    "😀 Emojis",
                    callback_data="bb_emojis",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💵 Minimum Buy",
                    callback_data="bb_minbuy",
                ),
                InlineKeyboardButton(
                    "🔘 Buttons",
                    callback_data="bb_buttons",
                ),
            ],
            [
                InlineKeyboardButton(
                    "👁 Preview",
                    callback_data="bb_preview",
                ),
                InlineKeyboardButton(
                    "♻️ Reset",
                    callback_data="bb_reset",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Close",
                    callback_data="bb_close",
                ),
            ],
        ]
    )


def chain_keyboard(
    prefix: str,
):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🟡 BNB",
                    callback_data=f"{prefix}_bnb",
                ),
                InlineKeyboardButton(
                    "🔷 Ethereum",
                    callback_data=f"{prefix}_ethereum",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🟣 Solana",
                    callback_data=f"{prefix}_solana",
                ),
                InlineKeyboardButton(
                    "🔴 Robinhood",
                    callback_data=f"{prefix}_robinhood",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=f"{prefix}_cancel",
                ),
            ],
        ]
    )


def emoji_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💚 Buy",
                    callback_data="bb_emoji_buy",
                ),
                InlineKeyboardButton(
                    "👤 Holder",
                    callback_data="bb_emoji_holder",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💰 Market Cap",
                    callback_data="bb_emoji_mcap",
                ),
                InlineKeyboardButton(
                    "💸 Spent",
                    callback_data="bb_emoji_spent",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📥 Received",
                    callback_data="bb_emoji_received",
                ),
                InlineKeyboardButton(
                    "🌐 Network",
                    callback_data="bb_emoji_network",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚙️ Settings",
                    callback_data="bb_settings",
                ),
            ],
        ]
    )


# ============================================================
# SETTINGS DATABASE
# ============================================================

async def ensure_buybot_settings(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO buybot_settings (
                group_id
            )
            VALUES ($1)
            ON CONFLICT (group_id)
            DO NOTHING
            """,
            group_id,
        )


async def get_buybot_settings(
    group_id: int,
):
    pool = await get_pool()

    await ensure_buybot_settings(
        group_id
    )

    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT *
            FROM buybot_settings
            WHERE group_id = $1
            """,
            group_id,
        )


async def update_setting(
    group_id: int,
    column: str,
    value,
):
    allowed = {
        "media_type",
        "media_id",
        "alert_title",
        "alert_template",
        "buy_emoji",
        "new_holder_emoji",
        "market_cap_emoji",
        "spent_emoji",
        "received_emoji",
        "network_emoji",
        "min_buy_usd",
    }

    if column not in allowed:
        raise ValueError(
            "Invalid BuyBot setting."
        )

    pool = await get_pool()

    await ensure_buybot_settings(
        group_id
    )

    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            UPDATE buybot_settings
            SET {column} = $1,
                updated_at = NOW()
            WHERE group_id = $2
            """,
            value,
            group_id,
        )


# ============================================================
# MAIN SETTINGS MENU
# ============================================================

async def show_buybot_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return

    chat = update.effective_chat

    if not chat:
        return

    await ensure_buybot_settings(
        chat.id
    )

    settings = await get_buybot_settings(
        chat.id
    )

    status = (
        "🟢 Enabled"
        if settings["enabled"]
        else "🔴 Disabled"
    )

    text = (
        "🤖 *BuyBot Settings*\n\n"
        f"Status: {status}\n"
        f"Minimum Buy: ${settings['min_buy_usd']}\n\n"
        "Manage how BuyBot posts purchase alerts "
        "in this group."
    )

    keyboard = settings_keyboard()

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        except Exception:
            await update.effective_chat.send_message(
                text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )


# ============================================================
# /BUYBOT
# ============================================================

async def buybot_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    await show_buybot_settings(
        update,
        context,
    )

    return SETTINGS


# ============================================================
# ACTIVATE / REMOVE BUYBOT
# ============================================================

async def activate_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return

    chat = update.effective_chat

    if not chat:
        return

    await ensure_buybot_settings(
        chat.id
    )

    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE buybot_settings
            SET enabled = TRUE,
                updated_at = NOW()
            WHERE group_id = $1
            """,
            chat.id,
        )

    await update.message.reply_text(
        "🟢 BuyBot is now active in this group."
    )


async def remove_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return

    chat = update.effective_chat

    if not chat:
        return

    await ensure_buybot_settings(
        chat.id
    )

    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE buybot_settings
            SET enabled = FALSE,
                updated_at = NOW()
            WHERE group_id = $1
            """,
            chat.id,
        )

    await update.message.reply_text(
        "🔴 BuyBot has been disabled in this group."
    )


# ============================================================
# ADD TOKEN
# ============================================================

async def add_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    await update.message.reply_text(
        "Select the blockchain for the token:",
        reply_markup=chain_keyboard(
            "bb_add"
        ),
    )

    return SELECT_TOKEN_CHAIN


async def add_chain_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    value = query.data.replace(
        "bb_add_",
        "",
        1,
    )

    if value == "cancel":
        await query.edit_message_text(
            "❌ Token addition cancelled."
        )
        return ConversationHandler.END

    if value not in SUPPORTED_CHAINS:
        await query.edit_message_text(
            "❌ Unsupported chain."
        )
        return ConversationHandler.END

    context.user_data[
        "buybot_chain"
    ] = value

    await query.edit_message_text(
        f"Selected: {SUPPORTED_CHAINS[value]}\n\n"
        "Now send the token contract address."
    )

    return ENTER_TOKEN_ADDRESS


def valid_token_address(
    chain: str,
    address: str,
) -> bool:
    address = address.strip()

    if chain in (
        "bnb",
        "ethereum",
        "robinhood",
    ):
        return bool(
            re.fullmatch(
                r"0x[a-fA-F0-9]{40}",
                address,
            )
        )

    if chain == "solana":
        return bool(
            re.fullmatch(
                r"[1-9A-HJ-NP-Za-km-z]{32,44}",
                address,
            )
        )

    return False


async def discover_token(
    chain: str,
    address: str,
):
    """
    Try to discover token information through
    DexScreener. If no result is found, the token
    can still be saved for pre-launch monitoring.
    """

    try:
        url = (
            "https://api.dexscreener.com"
            f"/token-pairs/v1/{chain}/{address}"
        )

        async with httpx.AsyncClient(
            timeout=10
        ) as client:
            response = await client.get(
                url
            )

            if response.status_code != 200:
                return None

            data = response.json()

            if not isinstance(
                data,
                list,
            ):
                return None

            if not data:
                return None

            pair = data[0]

            base_token = pair.get(
                "baseToken",
                {},
            )

            return {
                "name": base_token.get(
                    "name"
                ),
                "symbol": base_token.get(
                    "symbol"
                ),
                "pair_address": pair.get(
                    "pairAddress"
                ),
                "dex_url": pair.get(
                    "url"
                ),
            }

    except Exception:
        return None


async def token_address_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    address = (
        update.message.text
        .strip()
    )

    chain = context.user_data.get(
        "buybot_chain"
    )

    if not chain:
        await update.message.reply_text(
            "❌ Chain selection expired. Please use /add again."
        )
        return ConversationHandler.END

    if not valid_token_address(
        chain,
        address,
    ):
        await update.message.reply_text(
            "❌ Invalid contract address for the selected chain.\n\n"
            "Please send a valid address."
        )
        return ENTER_TOKEN_ADDRESS

    token_info = await discover_token(
        chain,
        address,
    )

    name = None
    symbol = None
    pair_address = None
    dex_url = None

    if token_info:
        name = token_info.get(
            "name"
        )
        symbol = token_info.get(
            "symbol"
        )
        pair_address = token_info.get(
            "pair_address"
        )
        dex_url = token_info.get(
            "dex_url"
        )

    pool = await get_pool()

    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            """
            SELECT id
            FROM buybot_tokens
            WHERE group_id = $1
              AND chain = $2
              AND LOWER(contract_address) = LOWER($3)
            """,
            chat.id,
            chain,
            address,
        )

        if existing:
            await update.message.reply_text(
                "⚠️ This token is already being monitored in this group."
            )
            return ConversationHandler.END

        await conn.execute(
            """
            INSERT INTO buybot_tokens (
                group_id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                pair_address,
                dex_url,
                enabled
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7,
                TRUE
            )
            """,
            chat.id,
            chain,
            address,
            name,
            symbol,
            pair_address,
            dex_url,
        )

    context.user_data.pop(
        "buybot_chain",
        None,
    )

    display_name = (
        f"{name} (${symbol})"
        if name and symbol
        else address
    )

    if pair_address:
        message = (
            "✅ *Token added to BuyBot monitoring.*\n\n"
            f"Token: {display_name}\n"
            f"Chain: {SUPPORTED_CHAINS[chain]}\n\n"
            "Buy alerts will be detected when qualifying "
            "transactions occur."
        )
    else:
        message = (
            "✅ *Token added.*\n\n"
            f"Address: `{address}`\n"
            f"Chain: {SUPPORTED_CHAINS[chain]}\n\n"
            "No live trading pair was found yet. "
            "The token remains saved for monitoring."
        )

    await update.message.reply_text(
        message,
        parse_mode="Markdown",
    )

    return ConversationHandler.END


# ============================================================
# REMOVE TOKEN
# ============================================================

async def remove_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id,
                chain,
                contract_address,
                token_name,
                token_symbol
            FROM buybot_tokens
            WHERE group_id = $1
            ORDER BY created_at DESC
            """,
            chat.id,
        )

    if not rows:
        await update.message.reply_text(
            "📭 No BuyBot tokens are currently saved."
        )
        return ConversationHandler.END

    buttons = []

    for row in rows:
        label = (
            f"{row['token_symbol']} — "
            f"{SUPPORTED_CHAINS.get(row['chain'], row['chain'])}"
            if row["token_symbol"]
            else
            f"{row['contract_address'][:8]}... — "
            f"{SUPPORTED_CHAINS.get(row['chain'], row['chain'])}"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=(
                        f"bb_remove_token_{row['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="bb_remove_cancel",
            )
        ]
    )

    await update.message.reply_text(
        "Select the token you want to remove:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return REMOVE_TOKEN


async def remove_token_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not await require_group_admin(update):
        return ConversationHandler.END

    value = query.data

    if value == "bb_remove_cancel":
        await query.edit_message_text(
            "❌ Token removal cancelled."
        )
        return ConversationHandler.END

    prefix = "bb_remove_token_"

    if not value.startswith(prefix):
        await query.edit_message_text(
            "❌ Invalid selection."
        )
        return ConversationHandler.END

    try:
        token_id = int(
            value[len(prefix):]
        )
    except ValueError:
        await query.edit_message_text(
            "❌ Invalid token selection."
        )
        return ConversationHandler.END

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE buybot_tokens
            SET enabled = FALSE,
                updated_at = NOW()
            WHERE id = $1
              AND group_id = $2
            RETURNING
                token_symbol,
                contract_address
            """,
            token_id,
            chat.id,
        )

    if not row:
        await query.edit_message_text(
            "❌ Token not found."
        )
        return ConversationHandler.END

    label = (
        row["token_symbol"]
        or row["contract_address"]
    )

    await query.edit_message_text(
        f"✅ `{label}` has been removed from active BuyBot monitoring.",
        parse_mode="Markdown",
    )

    return ConversationHandler.END


# ============================================================
# TOKEN LIST
# ============================================================

async def list_tokens(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat = update.effective_chat

    if not chat:
        return

    if chat.type not in (
        "group",
        "supergroup",
    ):
        await update.message.reply_text(
            "📋 /tokens is available inside the BuyBot group."
        )
        return

    if not await require_group_admin(update):
        return

    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                chain,
                contract_address,
                token_name,
                token_symbol,
                enabled
            FROM buybot_tokens
            WHERE group_id = $1
            ORDER BY created_at DESC
            """,
            chat.id,
        )

    if not rows:
        await update.message.reply_text(
            "📭 No tokens have been added yet.\n\n"
            "Use /add to add one."
        )
        return

    active = []
    disabled = []

    for row in rows:
        symbol = (
            row["token_symbol"]
            or "Unknown"
        )

        address = row[
            "contract_address"
        ]

        line = (
            f"• *{symbol}* — "
            f"{SUPPORTED_CHAINS.get(row['chain'], row['chain'])}\n"
            f"  `{address}`"
        )

        if row["enabled"]:
            active.append(line)
        else:
            disabled.append(line)

    text = "📋 *BuyBot Tokens*\n\n"

    if active:
        text += (
            "🟢 *Active*\n"
            + "\n".join(active)
            + "\n\n"
        )

    if disabled:
        text += (
            "🔴 *Disabled*\n"
            + "\n".join(disabled)
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
    )


# ============================================================
# MEDIA
# ============================================================

async def media_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🖼 *BuyBot Media*\n\n"
        "Send a photo, GIF, or video in your next message.\n\n"
        "The media will be used with BuyBot alerts.",
        parse_mode="Markdown",
    )

    return ENTER_MEDIA


async def media_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    message = update.message

    media_type = None
    media_id = None

    if message.photo:
        media_type = "photo"
        media_id = message.photo[-1].file_id

    elif message.animation:
        media_type = "animation"
        media_id = message.animation.file_id

    elif message.video:
        media_type = "video"
        media_id = message.video.file_id

    else:
        await message.reply_text(
            "❌ Please send a photo, GIF, or video."
        )
        return ENTER_MEDIA

    await update_setting(
        update.effective_chat.id,
        "media_type",
        media_type,
    )

    await update_setting(
        update.effective_chat.id,
        "media_id",
        media_id,
    )

    await message.reply_text(
        "✅ BuyBot media updated."
    )

    await show_buybot_settings(
        update,
        context,
    )

    return SETTINGS


async def reset_media_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    await update_setting(
        update.effective_chat.id,
        "media_type",
        None,
    )

    await update_setting(
        update.effective_chat.id,
        "media_id",
        None,
    )

    await update.message.reply_text(
        "✅ BuyBot media has been removed."
    )

    return ConversationHandler.END


# ============================================================
# TITLE
# ============================================================

async def title_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "✏️ Send the BuyBot alert title.\n\n"
        "Example:\n"
        "`🚀 New Buy Detected!`",
        parse_mode="Markdown",
    )

    return ENTER_TITLE


async def title_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    title = update.message.text.strip()

    if len(title) > 200:
        await update.message.reply_text(
            "❌ Title is too long. Keep it under 200 characters."
        )
        return ENTER_TITLE

    await update_setting(
        update.effective_chat.id,
        "alert_title",
        title,
    )

    await update.message.reply_text(
        "✅ BuyBot title updated."
    )

    await show_buybot_settings(
        update,
        context,
    )

    return SETTINGS


# ============================================================
# TEMPLATE
# ============================================================

async def template_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "📝 Send your BuyBot message template.\n\n"
        "Available placeholders:\n"
        "`{name}` — token name\n"
        "`{symbol}` — token symbol\n"
        "`{spent}` — amount spent\n"
        "`{received}` — tokens received\n"
        "`{market_cap}` — market cap\n"
        "`{chain}` — blockchain\n"
        "`{buyer}` — buyer address\n"
        "`{tx}` — transaction hash\n\n"
        "Example:\n"
        "`{name} (${symbol})\\n"
        "{spent} USDT spent\\n"
        "{received} tokens received`",
        parse_mode="Markdown",
    )

    return ENTER_TEMPLATE


async def template_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    template = update.message.text.strip()

    if len(template) > 3000:
        await update.message.reply_text(
            "❌ Template is too long."
        )
        return ENTER_TEMPLATE

    await update_setting(
        update.effective_chat.id,
        "alert_template",
        template,
    )

    await update.message.reply_text(
        "✅ BuyBot template updated."
    )

    await show_buybot_settings(
        update,
        context,
    )

    return SETTINGS


# ============================================================
# EMOJIS
# ============================================================

async def emojis_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "😀 *Choose which BuyBot emoji you want to change:*",
        reply_markup=emoji_keyboard(),
        parse_mode="Markdown",
    )

    return SETTINGS


async def emoji_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    mapping = {
        "bb_emoji_buy": (
            "buy_emoji",
            "Buy",
        ),
        "bb_emoji_holder": (
            "new_holder_emoji",
            "New Holder",
        ),
        "bb_emoji_mcap": (
            "market_cap_emoji",
            "Market Cap",
        ),
        "bb_emoji_spent": (
            "spent_emoji",
            "Spent",
        ),
        "bb_emoji_received": (
            "received_emoji",
            "Received",
        ),
        "bb_emoji_network": (
            "network_emoji",
            "Network",
        ),
    }

    item = mapping.get(
        query.data
    )

    if not item:
        return SETTINGS

    column, label = item

    context.user_data[
        "buybot_emoji_column"
    ] = column

    context.user_data[
        "buybot_emoji_label"
    ] = label

    await query.edit_message_text(
        f"😀 Send the emoji for *{label}*.\n\n"
        "You can send a normal emoji or a Telegram custom emoji.",
        parse_mode="Markdown",
    )

    return ENTER_EMOJI


async def emoji_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    column = context.user_data.get(
        "buybot_emoji_column"
    )

    if not column:
        return SETTINGS

    value = update.message.text

    if not value:
        await update.message.reply_text(
            "❌ Please send an emoji."
        )
        return ENTER_EMOJI

    await update_setting(
        update.effective_chat.id,
        column,
        value,
    )

    context.user_data.pop(
        "buybot_emoji_column",
        None,
    )

    context.user_data.pop(
        "buybot_emoji_label",
        None,
    )

    await update.message.reply_text(
        "✅ BuyBot emoji updated."
    )

    await show_buybot_settings(
        update,
        context,
    )

    return SETTINGS


# ============================================================
# MINIMUM BUY
# ============================================================

async def minbuy_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "💵 Send the minimum USD value required "
        "for a BuyBot alert.\n\n"
        "Example: `25`",
        parse_mode="Markdown",
    )

    return ENTER_MIN_BUY


async def minbuy_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    value = update.message.text.strip()

    try:
        amount = float(value)
    except ValueError:
        await update.message.reply_text(
            "❌ Please enter a valid number."
        )
        return ENTER_MIN_BUY

    if amount < 0:
        await update.message.reply_text(
            "❌ Minimum buy cannot be negative."
        )
        return ENTER_MIN_BUY

    if amount > 1000000000:
        await update.message.reply_text(
            "❌ Amount is too large."
        )
        return ENTER_MIN_BUY

    await update_setting(
        update.effective_chat.id,
        "min_buy_usd",
        amount,
    )

    await update.message.reply_text(
        f"✅ Minimum BuyBot amount set to ${amount:,.2f}."
    )

    await show_buybot_settings(
        update,
        context,
    )

    return SETTINGS


# ============================================================
# CUSTOM BUTTONS
# ============================================================

async def buttons_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    group_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id,
                button_name,
                button_url,
                position
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC
            """,
            group_id,
        )

    buttons = []

    for row in rows:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🔘 {row['button_name']}",
                    url=row["button_url"],
                )
            ]
        )

    if len(rows) < 3:
        buttons.append(
            [
                InlineKeyboardButton(
                    "➕ Add Button",
                    callback_data="bb_button_add",
                )
            ]
        )

    if rows:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🗑 Clear All Buttons",
                    callback_data="bb_button_clear",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⚙️ Settings",
                callback_data="bb_settings",
            )
        ]
    )

    text = (
        "🔘 *BuyBot Custom Buttons*\n\n"
        f"Buttons: {len(rows)}/3\n\n"
        "You can add up to 3 custom buttons."
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
        parse_mode="Markdown",
    )

    return SETTINGS


async def button_add_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    group_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_buttons
            WHERE group_id = $1
            """,
            group_id,
        )

    if count >= 3:
        await query.edit_message_text(
            "❌ You already have the maximum of 3 custom buttons."
        )
        return SETTINGS

    await query.edit_message_text(
        "🔘 Send the button in this format:\n\n"
        "`Button Name | https://example.com`\n\n"
        "Example:\n"
        "`Chart | https://dexscreener.com/`\n\n"
        "Maximum: 3 buttons.",
        parse_mode="Markdown",
    )

    return ENTER_BUTTON


async def button_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    text = update.message.text.strip()

    if "|" not in text:
        await update.message.reply_text(
            "❌ Invalid format.\n\n"
            "Use:\n"
            "`Button Name | https://example.com`",
            parse_mode="Markdown",
        )
        return ENTER_BUTTON

    name, url = [
        part.strip()
        for part in text.split(
            "|",
            1,
        )
    ]

    if not name or not url:
        await update.message.reply_text(
            "❌ Both button name and URL are required."
        )
        return ENTER_BUTTON

    if len(name) > 40:
        await update.message.reply_text(
            "❌ Button name is too long."
        )
        return ENTER_BUTTON

    if not re.match(
        r"^https?://",
        url,
        re.IGNORECASE,
    ):
        await update.message.reply_text(
            "❌ Button URL must start with http:// or https://"
        )
        return ENTER_BUTTON

    group_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_buttons
            WHERE group_id = $1
            """,
            group_id,
        )

        if count >= 3:
            await update.message.reply_text(
                "❌ Maximum of 3 buttons reached."
            )
            return SETTINGS

        position = await conn.fetchval(
            """
            SELECT COALESCE(
                MAX(position),
                -1
            ) + 1
            FROM buybot_buttons
            WHERE group_id = $1
            """,
            group_id,
        )

        await conn.execute(
            """
            INSERT INTO buybot_buttons (
                group_id,
                button_name,
                button_url,
                position
            )
            VALUES (
                $1,
                $2,
                $3,
                $4
            )
            """,
            group_id,
            name,
            url,
            position,
        )

    await update.message.reply_text(
        f"✅ Button `{name}` added.",
        parse_mode="Markdown",
    )

    await show_buybot_settings(
        update,
        context,
    )

    return SETTINGS


async def clear_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    group_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM buybot_buttons
            WHERE group_id = $1
            """,
            group_id,
        )

    await query.edit_message_text(
        "✅ All custom BuyBot buttons have been removed.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⚙️ Settings",
                        callback_data="bb_settings",
                    )
                ]
            ]
        ),
    )

    return SETTINGS


# ============================================================
# PREVIEW
# ============================================================

def render_preview(
    settings,
):
    title = (
        settings["alert_title"]
        or "🚀 New Buy Detected"
    )

    template = (
        settings["alert_template"]
        or
        "{name} (${symbol})\n\n"
        "{spent} spent\n"
        "{received} received\n"
        "{market_cap} market cap\n"
        "{chain}"
    )

    replacements = {
        "{name}": "Example Token",
        "{symbol}": "EXM",
        "{spent}": "$250",
        "{received}": "12,500 EXM",
        "{market_cap}": "$2.4M",
        "{chain}": "BNB Smart Chain",
        "{buyer}": "0x1234...abcd",
        "{tx}": "0x9876...4321",
    }

    for key, value in replacements.items():
        template = template.replace(
            key,
            value,
        )

    return (
        f"{title}\n\n"
        f"{template}"
    )


async def preview_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    settings = await get_buybot_settings(
        update.effective_chat.id
    )

    preview = render_preview(
        settings
    )

    pool = await get_pool()

    async with pool.acquire() as conn:
        buttons = await conn.fetch(
            """
            SELECT
                button_name,
                button_url
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC
            """,
            update.effective_chat.id,
        )

    keyboard = []

    for row in buttons:
        keyboard.append(
            [
                InlineKeyboardButton(
                    row["button_name"],
                    url=row["button_url"],
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⚙️ Settings",
                callback_data="bb_settings",
            )
        ]
    )

    try:
        await query.message.reply_text(
            preview,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )
    except Exception:
        pass

    return SETTINGS


# ============================================================
# RESET
# ============================================================

async def reset_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "⚠️ *Reset BuyBot settings?*\n\n"
        "This will reset the BuyBot configuration "
        "for this group.\n\n"
        "Your monitored token list will not be deleted.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Reset",
                        callback_data="bb_reset_confirm",
                    ),
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="bb_settings",
                    ),
                ]
            ]
        ),
        parse_mode="Markdown",
    )

    return SETTINGS


async def reset_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    group_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE buybot_settings
            SET
                enabled = FALSE,
                min_buy_usd = 0,
                media_type = NULL,
                media_id = NULL,
                alert_title = NULL,
                alert_template = NULL,
                buy_emoji = NULL,
                new_holder_emoji = NULL,
                market_cap_emoji = NULL,
                spent_emoji = NULL,
                received_emoji = NULL,
                network_emoji = NULL,
                updated_at = NOW()
            WHERE group_id = $1
            """,
            group_id,
        )

        await conn.execute(
            """
            DELETE FROM buybot_buttons
            WHERE group_id = $1
            """,
            group_id,
        )

    await query.edit_message_text(
        "♻️ BuyBot settings have been reset.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⚙️ Open Settings",
                        callback_data="bb_settings",
                    )
                ]
            ]
        ),
    )

    return SETTINGS


# ============================================================
# SETTINGS CALLBACK ROUTER
# ============================================================

async def settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    data = query.data

    if data == "bb_settings":
        await query.answer()

        await show_buybot_settings(
            update,
            context,
        )

        return SETTINGS

    if data == "bb_close":
        await query.answer()

        try:
            await query.edit_message_text(
                "✅ BuyBot settings closed."
            )
        except Exception:
            pass

        return ConversationHandler.END

    if data == "bb_media":
        return await media_menu(
            update,
            context,
        )

    if data == "bb_title":
        return await title_menu(
            update,
            context,
        )

    if data == "bb_template":
        return await template_menu(
            update,
            context,
        )

    if data == "bb_emojis":
        return await emojis_menu(
            update,
            context,
        )

    if data.startswith(
        "bb_emoji_"
    ):
        return await emoji_selected(
            update,
            context,
        )

    if data == "bb_minbuy":
        return await minbuy_menu(
            update,
            context,
        )

    if data == "bb_buttons":
        return await buttons_menu(
            update,
            context,
        )

    if data == "bb_button_add":
        return await button_add_menu(
            update,
            context,
        )

    if data == "bb_button_clear":
        return await clear_buttons(
            update,
            context,
        )

    if data == "bb_preview":
        return await preview_buybot(
            update,
            context,
        )

    if data == "bb_reset":
        return await reset_settings(
            update,
            context,
        )

    if data == "bb_reset_confirm":
        return await reset_confirm(
            update,
            context,
        )

    if data == "bb_add_token":
        await query.answer()

        await query.edit_message_text(
            "Select the blockchain for the token:",
            reply_markup=chain_keyboard(
                "bb_add"
            ),
        )

        return SELECT_TOKEN_CHAIN

    if data == "bb_remove_token":
        await query.answer()

        chat = update.effective_chat

        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    id,
                    chain,
                    contract_address,
                    token_symbol
                FROM buybot_tokens
                WHERE group_id = $1
                  AND enabled = TRUE
                ORDER BY created_at DESC
                """,
                chat.id,
            )

        if not rows:
            await query.edit_message_text(
                "📭 There are no active tokens to remove.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⚙️ Settings",
                                callback_data="bb_settings",
                            )
                        ]
                    ]
                ),
            )

            return SETTINGS

        buttons = []

        for row in rows:
            symbol = (
                row["token_symbol"]
                or row["contract_address"][:10]
            )

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"🗑 {symbol} — "
                        f"{SUPPORTED_CHAINS.get(row['chain'], row['chain'])}",
                        callback_data=(
                            f"bb_remove_token_{row['id']}"
                        ),
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "⚙️ Settings",
                    callback_data="bb_settings",
                )
            ]
        )

        await query.edit_message_text(
            "Select the token to remove:",
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
        )

        return REMOVE_TOKEN

    return SETTINGS


# ============================================================
# CANCEL
# ============================================================

async def cancel_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop(
        "buybot_chain",
        None,
    )

    context.user_data.pop(
        "buybot_emoji_column",
        None,
    )

    context.user_data.pop(
        "buybot_emoji_label",
        None,
    )

    if update.message:
        await update.message.reply_text(
            "❌ BuyBot operation cancelled."
        )

    elif update.callback_query:
        await update.callback_query.answer()

        try:
            await update.callback_query.edit_message_text(
                "❌ BuyBot operation cancelled."
            )
        except Exception:
            pass

    return ConversationHandler.END


# ============================================================
# CONVERSATION BUILDER
# ============================================================

def build_buybot_conversation():
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "buybot",
                buybot_command,
            ),
            CommandHandler(
                "buybotsettings",
                buybot_command,
            ),
            CommandHandler(
                "add",
                add_command,
            ),
            CommandHandler(
                "remove",
                remove_command,
            ),
            CommandHandler(
                "buybotresetmedia",
                reset_media_command,
            ),
        ],

        states={

            # ------------------------------------------------
            # SETTINGS
            # ------------------------------------------------

            SETTINGS: [
                CallbackQueryHandler(
                    settings_callback,
                    pattern=r"^bb_",
                ),
            ],

            # ------------------------------------------------
            # ADD TOKEN
            # ------------------------------------------------

            SELECT_TOKEN_CHAIN: [
                CallbackQueryHandler(
                    add_chain_selected,
                    pattern=r"^bb_add_(bnb|ethereum|solana|robinhood|cancel)$",
                ),
            ],

            ENTER_TOKEN_ADDRESS: [
                # Message handler intentionally handled below
            ],

            # ------------------------------------------------
            # REMOVE TOKEN
            # ------------------------------------------------

            REMOVE_TOKEN: [
                CallbackQueryHandler(
                    remove_token_selected,
                    pattern=r"^bb_remove_(token_\d+|cancel)$",
                ),
            ],

            # ------------------------------------------------
            # CUSTOM BUTTON
            # ------------------------------------------------

            ENTER_BUTTON: [
                # Message handler intentionally handled below
            ],

            # ------------------------------------------------
            # TITLE
            # ------------------------------------------------

            ENTER_TITLE: [
                # Message handler intentionally handled below
            ],

            # ------------------------------------------------
            # TEMPLATE
            # ------------------------------------------------

            ENTER_TEMPLATE: [
                # Message handler intentionally handled below
            ],

            # ------------------------------------------------
            # EMOJI
            # ------------------------------------------------

            ENTER_EMOJI: [
                # Message handler intentionally handled below
            ],

            # ------------------------------------------------
            # MIN BUY
            # ------------------------------------------------

            ENTER_MIN_BUY: [
                # Message handler intentionally handled below
            ],

            # ------------------------------------------------
            # MEDIA
            # ------------------------------------------------

            ENTER_MEDIA: [
                # Message handler intentionally handled below
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel_buybot,
            ),
        ],

        allow_reentry=True,
    )
