from __future__ import annotations

import re
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from database.connection import get_pool


# ============================================================
# CONVERSATION STATES
# ============================================================

SELECT_TOKEN_CHAIN = 1
ENTER_TOKEN_ADDRESS = 2

REMOVE_TOKEN_SELECT = 3

SETTINGS_MENU = 4
SET_MIN_BUY = 5
SET_TITLE = 6
SET_TEMPLATE = 7
SET_BUY_EMOJI = 8
SET_HOLDER_EMOJI = 9
SET_MC_EMOJI = 10
SET_SPENT_EMOJI = 11
SET_RECEIVED_EMOJI = 12
SET_NETWORK_EMOJI = 13

MEDIA_MENU = 14
BUTTON_MENU = 15
BUTTON_NAME = 16
BUTTON_URL = 17


# ============================================================
# CHAIN CONFIGURATION
# ============================================================

CHAIN_NAMES = {
    "BNB": "BNB Smart Chain",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "RH": "Robinhood Chain",
}


# ============================================================
# HELPERS
# ============================================================

def is_group(update: Update) -> bool:
    chat = update.effective_chat

    if not chat:
        return False

    return chat.type in (
        "group",
        "supergroup",
    )


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

    member = await chat.get_member(
        user.id
    )

    return member.status in (
        "administrator",
        "creator",
    )


async def require_group_admin(
    update: Update,
) -> bool:

    if not is_group(update):

        if update.callback_query:

            await update.callback_query.answer(
                "This command can only be used inside a group.",
                show_alert=True,
            )

        elif update.message:

            await update.message.reply_text(
                "This BuyBot setting can only be used inside a group."
            )

        return False

    if not await is_group_admin(update):

        if update.callback_query:

            await update.callback_query.answer(
                "Only group admins can change BuyBot settings.",
                show_alert=True,
            )

        elif update.message:

            await update.message.reply_text(
                "Only group admins can change BuyBot settings."
            )

        return False

    return True


def valid_evm_address(
    address: str,
) -> bool:

    return bool(
        re.fullmatch(
            r"0x[a-fA-F0-9]{40}",
            address.strip(),
        )
    )


def valid_solana_address(
    address: str,
) -> bool:

    value = address.strip()

    if not 32 <= len(value) <= 44:
        return False

    return bool(
        re.fullmatch(
            r"[1-9A-HJ-NP-Za-km-z]+",
            value,
        )
    )


def valid_token_address(
    chain: str,
    address: str,
) -> bool:

    if chain in (
        "BNB",
        "ETH",
        "RH",
    ):
        return valid_evm_address(
            address
        )

    if chain == "SOL":
        return valid_solana_address(
            address
        )

    return False


async def get_token_by_id(
    token_id: int,
):

    pool = await get_pool()

    async with pool.acquire() as connection:

        return await connection.fetchrow(
            """
            SELECT *
            FROM buybot_tokens
            WHERE id = $1
            """,
            token_id,
        )


# ============================================================
# BUYBOT ACTIVATION
# ============================================================

async def activate_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
            """
            INSERT INTO buybot_settings (
                group_id,
                enabled
            )
            VALUES ($1, TRUE)
            ON CONFLICT (group_id)
            DO UPDATE SET
                enabled = TRUE,
                updated_at = NOW()
            """,
            chat.id,
        )

    await update.message.reply_text(
        "🤖 *BuyBot Activated*\n\n"
        "BuyBot is now active for this group.\n\n"
        "Group admins can use:\n"
        "• /buybot — Open BuyBot settings\n"
        "• /add — Add a monitored token\n"
        "• /remove — Remove a token\n"
        "• /tokens — View monitored tokens\n\n"
        "Real buy activity will be monitored "
        "for the configured tokens.",
        parse_mode="Markdown",
    )


async def remove_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
            """
            INSERT INTO buybot_settings (
                group_id,
                enabled
            )
            VALUES ($1, FALSE)
            ON CONFLICT (group_id)
            DO UPDATE SET
                enabled = FALSE,
                updated_at = NOW()
            """,
            chat.id,
        )

    await update.message.reply_text(
        "🛑 *BuyBot Disabled*\n\n"
        "BuyBot alerts have been disabled "
        "for this group.\n\n"
        "Your monitored tokens and settings "
        "have not been deleted.",
        parse_mode="Markdown",
    )


# ============================================================
# /TOKENS
# ============================================================

async def list_tokens(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as connection:

        rows = await connection.fetch(
            """
            SELECT
                id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                enabled
            FROM buybot_tokens
            WHERE group_id = $1
            ORDER BY enabled DESC, id ASC
            """,
            chat.id,
        )

    if not rows:

        await update.message.reply_text(
            "📋 *BuyBot Tokens*\n\n"
            "No tokens are currently configured.\n\n"
            "Use /add to add a token.",
            parse_mode="Markdown",
        )

        return

    lines = [
        "📋 *BuyBot Tokens*",
        "",
    ]

    for row in rows:

        status = (
            "🟢 Active"
            if row["enabled"]
            else "⚪ Disabled"
        )

        symbol = (
            row["token_symbol"]
            or "Unknown"
        )

        name = (
            row["token_name"]
            or "Unknown Token"
        )

        address = row[
            "contract_address"
        ]

        short_address = (
            f"{address[:6]}..."
            f"{address[-4:]}"
        )

        lines.append(
            f"{status} · *{symbol}*"
        )

        lines.append(
            f"  {name}"
        )

        lines.append(
            f"  Chain: {CHAIN_NAMES.get(row['chain'], row['chain'])}"
        )

        lines.append(
            f"  Address: `{short_address}`"
        )

        lines.append("")

    lines.append(
        "Use /add to add another token."
    )

    lines.append(
        "Use /remove to disable a token."
    )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# ============================================================
# ADD TOKEN
# ============================================================

async def add_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    context.user_data[
        "buybot_chain"
    ] = None

    keyboard = [
        [
            InlineKeyboardButton(
                "🟡 BNB",
                callback_data="bb_add_chain_BNB",
            ),
            InlineKeyboardButton(
                "🔷 Ethereum",
                callback_data="bb_add_chain_ETH",
            ),
        ],
        [
            InlineKeyboardButton(
                "🟣 Solana",
                callback_data="bb_add_chain_SOL",
            ),
            InlineKeyboardButton(
                "🟠 Robinhood",
                callback_data="bb_add_chain_RH",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="bb_add_cancel",
            ),
        ],
    ]

    await update.message.reply_text(
        "➕ *Add BuyBot Token*\n\n"
        "Choose the blockchain:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return SELECT_TOKEN_CHAIN


async def add_chain_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    data = query.data

    if data == "bb_add_cancel":

        await query.edit_message_text(
            "❌ Add token cancelled."
        )

        return ConversationHandler.END

    chain = data.replace(
        "bb_add_chain_",
        "",
    )

    if chain not in CHAIN_NAMES:

        await query.edit_message_text(
            "Invalid blockchain selection."
        )

        return ConversationHandler.END

    context.user_data[
        "buybot_chain"
    ] = chain

    await query.edit_message_text(
        f"➕ *Add {CHAIN_NAMES[chain]} Token*\n\n"
        "Send the token contract address.",
        parse_mode="Markdown",
    )

    return ENTER_TOKEN_ADDRESS


async def add_address_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    address = (
        update.message.text
        or ""
    ).strip()

    chain = context.user_data.get(
        "buybot_chain"
    )

    if not chain:

        await update.message.reply_text(
            "The chain selection expired. "
            "Please use /add again."
        )

        return ConversationHandler.END

    if not valid_token_address(
        chain,
        address,
    ):

        await update.message.reply_text(
            "❌ *Invalid contract address.*\n\n"
            f"Please send a valid "
            f"{CHAIN_NAMES[chain]} token address.",
            parse_mode="Markdown",
        )

        return ENTER_TOKEN_ADDRESS

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as connection:

        existing = await connection.fetchrow(
            """
            SELECT id, enabled
            FROM buybot_tokens
            WHERE group_id = $1
              AND chain = $2
              AND LOWER(contract_address)
                    = LOWER($3)
            LIMIT 1
            """,
            chat.id,
            chain,
            address,
        )

        if existing:

            if existing["enabled"]:

                await update.message.reply_text(
                    "⚠️ This token is already "
                    "being monitored in this group."
                )

                return ConversationHandler.END

            await connection.execute(
                """
                UPDATE buybot_tokens
                SET
                    enabled = TRUE,
                    updated_at = NOW()
                WHERE id = $1
                """,
                existing["id"],
            )

            await update.message.reply_text(
                "✅ This token was already in "
                "your BuyBot list and has now "
                "been re-enabled."
            )

            return ConversationHandler.END

        await connection.execute(
            """
            INSERT INTO buybot_tokens (
                group_id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                enabled
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                TRUE
            )
            """,
            chat.id,
            chain,
            address,
            "Unknown Token",
            "UNKNOWN",
        )

    await update.message.reply_text(
        "✅ *Token Added*\n\n"
        f"Chain: {CHAIN_NAMES[chain]}\n"
        f"Contract:\n`{address}`\n\n"
        "BuyBot will now monitor this token "
        "for real buy activity.\n\n"
        "You can use /tokens to view the "
        "current monitoring list.",
        parse_mode="Markdown",
    )

    context.user_data.pop(
        "buybot_chain",
        None,
    )

    return ConversationHandler.END


# ============================================================
# REMOVE TOKEN
# ============================================================

async def remove_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as connection:

        rows = await connection.fetch(
            """
            SELECT
                id,
                chain,
                token_name,
                token_symbol,
                contract_address
            FROM buybot_tokens
            WHERE group_id = $1
              AND enabled = TRUE
            ORDER BY id ASC
            """,
            chat.id,
        )

    if not rows:

        await update.message.reply_text(
            "📋 There are no active BuyBot "
            "tokens to remove."
        )

        return ConversationHandler.END

    keyboard = []

    for row in rows:

        symbol = (
            row["token_symbol"]
            or "UNKNOWN"
        )

        chain = row["chain"]

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"❌ {symbol} · {chain}",
                    callback_data=(
                        f"bb_remove_{row['id']}"
                    ),
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "Cancel",
                callback_data="bb_remove_cancel",
            )
        ]
    )

    await update.message.reply_text(
        "➖ *Remove BuyBot Token*\n\n"
        "Choose the token you want to "
        "disable:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return REMOVE_TOKEN_SELECT


async def remove_token_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    if query.data == "bb_remove_cancel":

        await query.edit_message_text(
            "❌ Remove cancelled."
        )

        return ConversationHandler.END

    try:

        token_id = int(
            query.data.replace(
                "bb_remove_",
                "",
            )
        )

    except ValueError:

        await query.edit_message_text(
            "Invalid token selection."
        )

        return ConversationHandler.END

    token = await get_token_by_id(
        token_id
    )

    if not token:

        await query.edit_message_text(
            "❌ Token not found."
        )

        return ConversationHandler.END

    if (
        token["group_id"]
        != update.effective_chat.id
    ):

        await query.edit_message_text(
            "❌ This token does not belong "
            "to this group."
        )

        return ConversationHandler.END

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
            """
            UPDATE buybot_tokens
            SET
                enabled = FALSE,
                updated_at = NOW()
            WHERE id = $1
            """,
            token_id,
        )

    symbol = (
        token["token_symbol"]
        or "UNKNOWN"
    )

    await query.edit_message_text(
        f"✅ *{symbol} removed from BuyBot monitoring.*\n\n"
        "The token remains saved and can be "
        "re-enabled later with /add.",
        parse_mode="Markdown",
    )

    return ConversationHandler.END


# ============================================================
# SETTINGS MAIN MENU
# ============================================================

async def buybot_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as connection:

        settings = await connection.fetchrow(
            """
            SELECT *
            FROM buybot_settings
            WHERE group_id = $1
            """,
            chat.id,
        )

    if not settings:

        pool = await get_pool()

        async with pool.acquire() as connection:

            await connection.execute(
                """
                INSERT INTO buybot_settings (
                    group_id,
                    enabled
                )
                VALUES ($1, TRUE)
                ON CONFLICT DO NOTHING
                """,
                chat.id,
            )

        async with pool.acquire() as connection:

            settings = await connection.fetchrow(
                """
                SELECT *
                FROM buybot_settings
                WHERE group_id = $1
                """,
                chat.id,
            )

    return await show_settings_menu(
        update,
        settings,
    )


async def show_settings_menu(
    update: Update,
    settings,
):

    enabled = (
        "🟢 ON"
        if settings["enabled"]
        else "🔴 OFF"
    )

    minimum = settings[
        "min_buy_usd"
    ]

    title = (
        settings["alert_title"]
        or "⚡ NEW BUY DETECTED"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                f"🤖 Status: {enabled}",
                callback_data="bb_setting_toggle",
            ),
        ],
        [
            InlineKeyboardButton(
                "💵 Minimum Buy",
                callback_data="bb_setting_min",
            ),
            InlineKeyboardButton(
                "📝 Alert Title",
                callback_data="bb_setting_title",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎨 Alert Template",
                callback_data="bb_setting_template",
            ),
        ],
        [
            InlineKeyboardButton(
                "😀 Emojis",
                callback_data="bb_setting_emojis",
            ),
            InlineKeyboardButton(
                "🖼 Media",
                callback_data="bb_setting_media",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔘 Buttons",
                callback_data="bb_setting_buttons",
            ),
        ],
        [
            InlineKeyboardButton(
                "📋 Tokens",
                callback_data="bb_setting_tokens",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Close",
                callback_data="bb_setting_close",
            ),
        ],
    ]

    text = (
        "🤖 *BUYBOT SETTINGS*\n\n"
        f"Status: {enabled}\n"
        f"Minimum Buy: `${minimum}`\n"
        f"Title: `{title}`\n\n"
        "Use the menu below to customize "
        "how BuyBot alerts appear in this group."
    )

    if update.callback_query:

        await update.callback_query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

    else:

        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

    return SETTINGS_MENU


# ============================================================
# SETTINGS CALLBACKS
# ============================================================

async def settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    data = query.data
    chat_id = update.effective_chat.id

    if data == "bb_setting_close":

        await query.edit_message_text(
            "BuyBot settings closed."
        )

        return ConversationHandler.END

    if data == "bb_setting_toggle":

        pool = await get_pool()

        async with pool.acquire() as connection:

            await connection.execute(
                """
                UPDATE buybot_settings
                SET
                    enabled = NOT enabled,
                    updated_at = NOW()
                WHERE group_id = $1
                """,
                chat_id,
            )

            settings = await connection.fetchrow(
                """
                SELECT *
                FROM buybot_settings
                WHERE group_id = $1
                """,
                chat_id,
            )

        return await show_settings_menu(
            update,
            settings,
        )

    if data == "bb_setting_min":

        await query.edit_message_text(
            "💵 *Minimum Buy Amount*\n\n"
            "Send the minimum USD value that "
            "should trigger a BuyBot alert.\n\n"
            "Example:\n"
            "`10`\n\n"
            "Send `0` to allow every detected buy.",
            parse_mode="Markdown",
        )

        return SET_MIN_BUY

    if data == "bb_setting_title":

        await query.edit_message_text(
            "📝 *Alert Title*\n\n"
            "Send the title you want BuyBot "
            "alerts to use.\n\n"
            "Example:\n"
            "`🚀 Fresh Buy Alert`",
            parse_mode="Markdown",
        )

        return SET_TITLE

    if data == "bb_setting_template":

        await query.edit_message_text(
            "🎨 *Alert Template*\n\n"
            "Send your custom alert template.\n\n"
            "Available placeholders:\n\n"
            "`{title}`\n"
            "`{token_name}`\n"
            "`{token_symbol}`\n"
            "`{spent}`\n"
            "`{received}`\n"
            "`{buyer_short}`\n"
            "`{market_cap}`\n"
            "`{network}`\n"
            "`{tx_short}`\n\n"
            "You can arrange them however you want.",
            parse_mode="Markdown",
        )

        return SET_TEMPLATE

    if data == "bb_setting_emojis":

        keyboard = [
            [
                InlineKeyboardButton(
                    "🟢 Buy Emoji",
                    callback_data="bb_emoji_buy",
                ),
                InlineKeyboardButton(
                    "👤 Holder Emoji",
                    callback_data="bb_emoji_holder",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💎 Market Cap Emoji",
                    callback_data="bb_emoji_mc",
                ),
                InlineKeyboardButton(
                    "💰 Spent Emoji",
                    callback_data="bb_emoji_spent",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📦 Received Emoji",
                    callback_data="bb_emoji_received",
                ),
                InlineKeyboardButton(
                    "⛓️ Network Emoji",
                    callback_data="bb_emoji_network",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="bb_emoji_back",
                ),
            ],
        ]

        await query.edit_message_text(
            "😀 *Customize BuyBot Emojis*\n\n"
            "Choose which alert element you "
            "want to change.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

        return SETTINGS_MENU

    if data == "bb_setting_media":

        await show_media_menu(
            update
        )

        return MEDIA_MENU

    if data == "bb_setting_buttons":

        await show_button_menu(
            update
        )

        return BUTTON_MENU

    if data == "bb_setting_tokens":

        await query.edit_message_text(
            "📋 *Token Management*\n\n"
            "Use:\n"
            "/add — Add a token\n"
            "/remove — Disable a token\n"
            "/tokens — View all tokens",
            parse_mode="Markdown",
        )

        return SETTINGS_MENU

    # Emoji callbacks

    emoji_states = {
        "bb_emoji_buy": SET_BUY_EMOJI,
        "bb_emoji_holder": SET_HOLDER_EMOJI,
        "bb_emoji_mc": SET_MC_EMOJI,
        "bb_emoji_spent": SET_SPENT_EMOJI,
        "bb_emoji_received": SET_RECEIVED_EMOJI,
        "bb_emoji_network": SET_NETWORK_EMOJI,
    }

    if data in emoji_states:

        context.user_data[
            "emoji_setting"
        ] = data.replace(
            "bb_emoji_",
            "",
        )

        await query.edit_message_text(
            "😀 Send the emoji you want "
            "BuyBot to use."
        )

        return emoji_states[data]

    if data == "bb_emoji_back":

        pool = await get_pool()

        async with pool.acquire() as connection:

            settings = await connection.fetchrow(
                """
                SELECT *
                FROM buybot_settings
                WHERE group_id = $1
                """,
                chat_id,
            )

        return await show_settings_menu(
            update,
            settings,
        )

    return SETTINGS_MENU


# ============================================================
# MINIMUM BUY
# ============================================================

async def set_min_buy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    try:

        value = float(
            update.message.text.strip()
        )

        if value < 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ Please send a valid positive "
            "USD amount.\n\n"
            "Example: `10`",
            parse_mode="Markdown",
        )

        return SET_MIN_BUY

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                min_buy_usd = $1,
                updated_at = NOW()
            WHERE group_id = $2
            """,
            value,
            update.effective_chat.id,
        )

        settings = await connection.fetchrow(
            """
            SELECT *
            FROM buybot_settings
            WHERE group_id = $1
            """,
            update.effective_chat.id,
        )

    await update.message.reply_text(
        f"✅ Minimum Buy updated to `${value}`.",
        parse_mode="Markdown",
    )

    await show_settings_menu(
        update,
        settings,
    )

    return SETTINGS_MENU


# ============================================================
# TITLE
# ============================================================

async def set_title(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    title = (
        update.message.text
        or ""
    ).strip()

    if not title:

        await update.message.reply_text(
            "❌ Title cannot be empty."
        )

        return SET_TITLE

    if len(title) > 100:

        await update.message.reply_text(
            "❌ Title is too long. "
            "Please keep it under 100 characters."
        )

        return SET_TITLE

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                alert_title = $1,
                updated_at = NOW()
            WHERE group_id = $2
            """,
            title,
            update.effective_chat.id,
        )

        settings = await connection.fetchrow(
            """
            SELECT *
            FROM buybot_settings
            WHERE group_id = $1
            """,
            update.effective_chat.id,
        )

    await update.message.reply_text(
        "✅ BuyBot alert title updated."
    )

    await show_settings_menu(
        update,
        settings,
    )

    return SETTINGS_MENU


# ============================================================
# TEMPLATE
# ============================================================

async def set_template(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    template = (
        update.message.text
        or ""
    ).strip()

    if not template:

        await update.message.reply_text(
            "❌ Template cannot be empty."
        )

        return SET_TEMPLATE

    if len(template) > 3000:

        await update.message.reply_text(
            "❌ Template is too long."
        )

        return SET_TEMPLATE

    allowed = {
        "title",
        "token_name",
        "token_symbol",
        "spent",
        "received",
        "buyer_short",
        "market_cap",
        "network",
        "tx_short",
    }

    placeholders = re.findall(
        r"{([a-zA-Z0-9_]+)}",
        template,
    )

    invalid = [
        item
        for item in placeholders
        if item not in allowed
    ]

    if invalid:

        await update.message.reply_text(
            "❌ Unknown placeholder(s):\n"
            + ", ".join(
                f"`{{{x}}}`"
                for x in invalid
            ),
            parse_mode="Markdown",
        )

        return SET_TEMPLATE

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                alert_template = $1,
                updated_at = NOW()
            WHERE group_id = $2
            """,
            template,
            update.effective_chat.id,
        )

        settings = await connection.fetchrow(
            """
            SELECT *
            FROM buybot_settings
            WHERE group_id = $1
            """,
            update.effective_chat.id,
        )

    await update.message.reply_text(
        "✅ BuyBot alert template updated."
    )

    await show_settings_menu(
        update,
        settings,
    )

    return SETTINGS_MENU


# ============================================================
# EMOJI SETTERS
# ============================================================

async def set_emoji(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    emoji = (
        update.message.text
        or ""
    ).strip()

    if not emoji:

        await update.message.reply_text(
            "❌ Please send an emoji."
        )

        return SETTINGS_MENU

    setting = context.user_data.get(
        "emoji_setting"
    )

    columns = {
        "buy": "buy_emoji",
        "holder": "new_holder_emoji",
        "mc": "market_cap_emoji",
        "spent": "spent_emoji",
        "received": "received_emoji",
        "network": "network_emoji",
    }

    column = columns.get(
        setting
    )

    if not column:

        await update.message.reply_text(
            "❌ Emoji setting expired. "
            "Please open /buybot again."
        )

        return ConversationHandler.END

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
            f"""
            UPDATE buybot_settings
            SET
                {column} = $1,
                updated_at = NOW()
            WHERE group_id = $2
            """,
            emoji,
            update.effective_chat.id,
        )

        settings = await connection.fetchrow(
            """
            SELECT *
            FROM buybot_settings
            WHERE group_id = $1
            """,
            update.effective_chat.id,
        )

    context.user_data.pop(
        "emoji_setting",
        None,
    )

    await update.message.reply_text(
        "✅ Emoji updated."
    )

    await show_settings_menu(
        update,
        settings,
    )

    return SETTINGS_MENU


# ============================================================
# MEDIA SETTINGS
# ============================================================

async def show_media_menu(
    update: Update,
):

    keyboard = [
        [
            InlineKeyboardButton(
                "🖼 Set Photo",
                callback_data="bb_media_photo",
            ),
            InlineKeyboardButton(
                "🎬 Set Video",
                callback_data="bb_media_video",
            ),
        ],
        [
            InlineKeyboardButton(
                "✨ Set GIF",
                callback_data="bb_media_animation",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Remove Media",
                callback_data="bb_media_remove",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="bb_media_back",
            ),
        ],
    ]

    text = (
        "🖼 *BuyBot Media*\n\n"
        "Choose what should appear above "
        "your BuyBot alert."
    )

    if update.callback_query:

        await update.callback_query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

    else:

        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )


async def media_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    data = query.data
    chat_id = update.effective_chat.id

    if data == "bb_media_back":

        pool = await get_pool()

        async with pool.acquire() as connection:

            settings = await connection.fetchrow(
                """
                SELECT *
                FROM buybot_settings
                WHERE group_id = $1
                """,
                chat_id,
            )

        return await show_settings_menu(
            update,
            settings,
        )

    if data == "bb_media_remove":

        pool = await get_pool()

        async with pool.acquire() as connection:

            await connection.execute(
                """
                UPDATE buybot_settings
                SET
                    media_type = NULL,
                    media_id = NULL,
                    updated_at = NOW()
                WHERE group_id = $1
                """,
                chat_id,
            )

        await query.edit_message_text(
            "🗑 BuyBot media removed."
        )

        return SETTINGS_MENU

    media_type = data.replace(
        "bb_media_",
        "",
    )

    context.user_data[
        "media_type"
    ] = media_type

    if media_type == "photo":

        text = (
            "🖼 Send the photo you want "
            "BuyBot to use."
        )

    elif media_type == "video":

        text = (
            "🎬 Send the video you want "
            "BuyBot to use."
        )

    else:

        text = (
            "✨ Send the GIF/animation you want "
            "BuyBot to use."
        )

    await query.edit_message_text(
        text
    )

    return SETTINGS_MENU


async def media_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    media_type = context.user_data.get(
        "media_type"
    )

    media_id: Optional[str] = None

    if media_type == "photo":

        if not update.message.photo:

            await update.message.reply_text(
                "❌ Please send a photo."
            )

            return SETTINGS_MENU

        media_id = (
            update.message.photo[-1].file_id
        )

    elif media_type == "video":

        if not update.message.video:

            await update.message.reply_text(
                "❌ Please send a video."
            )

            return SETTINGS_MENU

        media_id = (
            update.message.video.file_id
        )

    elif media_type == "animation":

        if not update.message.animation:

            await update.message.reply_text(
                "❌ Please send a GIF/animation."
            )

            return SETTINGS_MENU

        media_id = (
            update.message.animation.file_id
        )

    else:

        return SETTINGS_MENU

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                media_type = $1,
                media_id = $2,
                updated_at = NOW()
            WHERE group_id = $3
            """,
            media_type,
            media_id,
            update.effective_chat.id,
        )

        settings = await connection.fetchrow(
            """
            SELECT *
            FROM buybot_settings
            WHERE group_id = $1
            """,
            update.effective_chat.id,
        )

    context.user_data.pop(
        "media_type",
        None,
    )

    await update.message.reply_text(
        "✅ BuyBot media updated."
    )

    await show_settings_menu(
        update,
        settings,
    )

    return SETTINGS_MENU


# ============================================================
# CUSTOM BUTTONS
# ============================================================

async def show_button_menu(
    update: Update,
):

    chat_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as connection:

        buttons = await connection.fetch(
            """
            SELECT
                id,
                button_name,
                button_url,
                position
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC
            """,
            chat_id,
        )

    keyboard = []

    for button in buttons:

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🔘 {button['button_name']}",
                    url=button["button_url"],
                )
            ]
        )

    if len(buttons) < 3:

        keyboard.append(
            [
                InlineKeyboardButton(
                    "➕ Add Button",
                    callback_data="bb_button_add",
                )
            ]
        )

    if buttons:

        keyboard.append(
            [
                InlineKeyboardButton(
                    "🗑 Clear All Buttons",
                    callback_data="bb_button_clear",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="bb_button_back",
            )
        ]
    )

    text = (
        "🔘 *BuyBot Buttons*\n\n"
        f"Custom buttons: {len(buttons)}/3\n\n"
        "Each custom button uses:\n"
        "`Button Name | Button Link`\n\n"
        "Example:\n"
        "`Website | https://example.com`"
    )

    if update.callback_query:

        await update.callback_query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

    else:

        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )


async def button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    data = query.data
    chat_id = update.effective_chat.id

    if data == "bb_button_back":

        pool = await get_pool()

        async with pool.acquire() as connection:

            settings = await connection.fetchrow(
                """
                SELECT *
                FROM buybot_settings
                WHERE group_id = $1
                """,
                chat_id,
            )

        return await show_settings_menu(
            update,
            settings,
        )

    if data == "bb_button_add":

        pool = await get_pool()

        async with pool.acquire() as connection:

            count = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM buybot_buttons
                WHERE group_id = $1
                """,
                chat_id,
            )

        if count >= 3:

            await query.edit_message_text(
                "❌ Maximum of 3 custom buttons "
                "is allowed."
            )

            return BUTTON_MENU

        await query.edit_message_text(
            "🔘 *Add Custom Button*\n\n"
            "Send the button name.\n\n"
            "Example:\n"
            "`Website`",
            parse_mode="Markdown",
        )

        return BUTTON_NAME

    if data == "bb_button_clear":

        pool = await get_pool()

        async with pool.acquire() as connection:

            await connection.execute(
                """
                DELETE FROM buybot_buttons
                WHERE group_id = $1
                """,
                chat_id,
            )

        await query.edit_message_text(
            "🗑 All custom BuyBot buttons "
            "have been removed."
        )

        return BUTTON_MENU

    return BUTTON_MENU


async def button_name_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    name = (
        update.message.text
        or ""
    ).strip()

    if not name:

        await update.message.reply_text(
            "❌ Button name cannot be empty."
        )

        return BUTTON_NAME

    if len(name) > 32:

        await update.message.reply_text(
            "❌ Button name must be 32 "
            "characters or fewer."
        )

        return BUTTON_NAME

    context.user_data[
        "button_name"
    ] = name

    await update.message.reply_text(
        "🔗 Now send the button URL.\n\n"
        "Example:\n"
        "`https://example.com`",
        parse_mode="Markdown",
    )

    return BUTTON_URL


async def button_url_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    url = (
        update.message.text
        or ""
    ).strip()

    if not re.match(
        r"^https?://",
        url,
        re.IGNORECASE,
    ):

        await update.message.reply_text(
            "❌ Please send a valid HTTP/HTTPS URL."
        )

        return BUTTON_URL

    name = context.user_data.get(
        "button_name"
    )

    if not name:

        await update.message.reply_text(
            "❌ Button setup expired. "
            "Please open /buybot again."
        )

        return ConversationHandler.END

    pool = await get_pool()

    async with pool.acquire() as connection:

        count = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_buttons
            WHERE group_id = $1
            """,
            update.effective_chat.id,
        )

        if count >= 3:

            await update.message.reply_text(
                "❌ Maximum of 3 custom buttons "
                "is already configured."
            )

            return BUTTON_MENU

        position = int(count) + 1

        await connection.execute(
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
            update.effective_chat.id,
            name,
            url,
            position,
        )

    context.user_data.pop(
        "button_name",
        None,
    )

    await update.message.reply_text(
        f"✅ Button `{name}` added.",
        parse_mode="Markdown",
    )

    await show_button_menu(
        update
    )

    return BUTTON_MENU


# ============================================================
# CONVERSATION HANDLER
# ============================================================

def build_buybot_conversation():

    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "buybot",
                buybot_settings,
            ),
            CommandHandler(
                "buybotsettings",
                buybot_settings,
            ),
            CommandHandler(
                "add",
                add_start,
            ),
            CommandHandler(
                "remove",
                remove_start,
            ),
        ],

        states={

            SELECT_TOKEN_CHAIN: [
                CallbackQueryHandler(
                    add_chain_selected,
                    pattern=r"^bb_add_chain_|^bb_add_cancel$",
                ),
            ],

            ENTER_TOKEN_ADDRESS: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    add_address_received,
                ),
            ],

            REMOVE_TOKEN_SELECT: [
                CallbackQueryHandler(
                    remove_token_selected,
                    pattern=r"^bb_remove_|^bb_remove_cancel$",
                ),
            ],

            SETTINGS_MENU: [
                CallbackQueryHandler(
                    settings_callback,
                    pattern=(
                        r"^bb_setting_"
                        r"|^bb_emoji_"
                    ),
                ),
                CallbackQueryHandler(
                    media_callback,
                    pattern=r"^bb_media_",
                ),
                CallbackQueryHandler(
                    button_callback,
                    pattern=r"^bb_button_",
                ),
                MessageHandler(
                    filters.PHOTO
                    | filters.VIDEO
                    | filters.ANIMATION,
                    media_received,
                ),
            ],

            SET_MIN_BUY: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    set_min_buy,
                ),
            ],

            SET_TITLE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    set_title,
                ),
            ],

            SET_TEMPLATE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    set_template,
                ),
            ],

            SET_BUY_EMOJI: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    set_emoji,
                ),
            ],

            SET_HOLDER_EMOJI: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    set_emoji,
                ),
            ],

            SET_MC_EMOJI: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    set_emoji,
                ),
            ],

            SET_SPENT_EMOJI: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    set_emoji,
                ),
            ],

            SET_RECEIVED_EMOJI: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    set_emoji,
                ),
            ],

            SET_NETWORK_EMOJI: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    set_emoji,
                ),
            ],

            MEDIA_MENU: [
                CallbackQueryHandler(
                    media_callback,
                    pattern=r"^bb_media_",
                ),
                MessageHandler(
                    filters.PHOTO
                    | filters.VIDEO
                    | filters.ANIMATION,
                    media_received,
                ),
            ],

            BUTTON_MENU: [
                CallbackQueryHandler(
                    button_callback,
                    pattern=r"^bb_button_",
                ),
            ],

            BUTTON_NAME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    button_name_received,
                ),
            ],

            BUTTON_URL: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    button_url_received,
                ),
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
        "emoji_setting",
        None,
    )

    context.user_data.pop(
        "media_type",
        None,
    )

    context.user_data.pop(
        "button_name",
        None,
    )

    if update.callback_query:

        await update.callback_query.answer()

        await update.callback_query.edit_message_text(
            "❌ BuyBot setup cancelled."
        )

    elif update.message:

        await update.message.reply_text(
            "❌ BuyBot setup cancelled."
        )

    return ConversationHandler.END
