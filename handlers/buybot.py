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
    MessageHandler,
    filters,
)

from database.connection import get_pool


# =========================================================
# CONSTANTS
# =========================================================

SUPPORTED_CHAINS = {
    "bnb": {
        "name": "BNB Smart Chain",
        "dex_chain": "bsc",
    },
    "ethereum": {
        "name": "Ethereum",
        "dex_chain": "ethereum",
    },
    "solana": {
        "name": "Solana",
        "dex_chain": "solana",
    },
    "robinhood": {
        "name": "Robinhood Chain",
        "dex_chain": "robinhood",
    },
}


(
    BUYBOT_SETTINGS,
    SELECT_TOKEN_CHAIN,
    ENTER_TOKEN_ADDRESS,
    SELECT_REMOVE_TOKEN,
    ENTER_BUTTON,
    ENTER_TITLE,
    ENTER_TEMPLATE,
    ENTER_EMOJI,
    ENTER_MIN_BUY,
    ENTER_MEDIA,
) = range(10)


# =========================================================
# GENERAL HELPERS
# =========================================================


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
    if not is_group(update):
        return False

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
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
    allowed = await is_group_admin(update)

    if allowed:
        return True

    target = (
        update.callback_query.message
        if update.callback_query
        else update.message
    )

    if target:
        await target.reply_text(
            "🔒 This BuyBot setting can only "
            "be changed by a group administrator."
        )

    return False


# =========================================================
# KEYBOARDS
# =========================================================


def settings_keyboard():
    return InlineKeyboardMarkup(
        [
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
                    "💰 Minimum Buy",
                    callback_data="bb_minbuy",
                ),
                InlineKeyboardButton(
                    "🔘 Buttons",
                    callback_data="bb_buttons",
                ),
            ],
            [
                InlineKeyboardButton(
                    "👀 Preview",
                    callback_data="bb_preview",
                ),
            ],
            [
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


def emoji_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🟢 Buy",
                    callback_data="bb_emoji_buy",
                ),
                InlineKeyboardButton(
                    "👤 Holder",
                    callback_data="bb_emoji_holder",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💎 Market Cap",
                    callback_data="bb_emoji_market",
                ),
                InlineKeyboardButton(
                    "💸 Spent",
                    callback_data="bb_emoji_spent",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🪙 Received",
                    callback_data="bb_emoji_received",
                ),
                InlineKeyboardButton(
                    "⛓️ Network",
                    callback_data="bb_emoji_network",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="bb_settings",
                ),
            ],
        ]
    )


# =========================================================
# DATABASE SETTINGS
# =========================================================


async def get_settings(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT
                group_id,
                enabled,
                min_buy_usd,
                media_type,
                media_id,
                alert_title,
                alert_template,
                buy_emoji,
                new_holder_emoji,
                market_cap_emoji,
                spent_emoji,
                received_emoji,
                network_emoji
            FROM buybot_settings
            WHERE group_id = $1;
            """,
            group_id,
        )


async def ensure_settings(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO buybot_settings (
                group_id,
                enabled,
                min_buy_usd
            )
            VALUES (
                $1,
                FALSE,
                0
            )
            ON CONFLICT (group_id)
            DO NOTHING;
            """,
            group_id,
        )


# =========================================================
# MAIN BUYBOT MENU
# =========================================================


async def show_buybot_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return BUYBOT_SETTINGS

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    await ensure_settings(
        chat.id
    )

    settings = await get_settings(
        chat.id
    )

    enabled = (
        "🟢 Active"
        if settings
        and settings["enabled"]
        else "🔴 Inactive"
    )

    minimum = (
        float(settings["min_buy_usd"])
        if settings
        and settings["min_buy_usd"] is not None
        else 0
    )

    text = (
        "🤖 *THE BLOCK ROOM — BUYBOT*\n\n"
        f"Status: {enabled}\n"
        f"Minimum Buy: `${minimum:,.2f}`\n\n"
        "Customize how BuyBot messages appear "
        "in this group.\n\n"
        "Choose a setting below:"
    )

    target = (
        update.callback_query.message
        if update.callback_query
        else update.message
    )

    if not target:
        return BUYBOT_SETTINGS

    await target.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=settings_keyboard(),
    )

    return BUYBOT_SETTINGS


async def buybot_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    return await show_buybot_settings(
        update,
        context,
    )


# =========================================================
# ACTIVATE / REMOVE BUYBOT
# =========================================================


async def activate_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    from config import ADMIN_IDS

    user = update.effective_user

    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text(
            "❌ Admin access required."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "`/activebuybot <group_id>`",
            parse_mode="Markdown",
        )
        return

    try:
        group_id = int(
            context.args[0]
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid group ID."
        )
        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO buybot_settings (
                group_id,
                enabled,
                min_buy_usd
            )
            VALUES (
                $1,
                TRUE,
                0
            )
            ON CONFLICT (group_id)
            DO UPDATE SET
                enabled = TRUE,
                updated_at = NOW();
            """,
            group_id,
        )

    await update.message.reply_text(
        f"✅ BuyBot activated for `{group_id}`.",
        parse_mode="Markdown",
    )


async def remove_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    from config import ADMIN_IDS

    user = update.effective_user

    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text(
            "❌ Admin access required."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "`/removebuybot <group_id>`",
            parse_mode="Markdown",
        )
        return

    try:
        group_id = int(
            context.args[0]
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid group ID."
        )
        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                enabled = FALSE,
                updated_at = NOW()
            WHERE group_id = $1;
            """,
            group_id,
        )

    await update.message.reply_text(
        f"🔴 BuyBot disabled for `{group_id}`.",
        parse_mode="Markdown",
    )


# =========================================================
# TOKEN VALIDATION
# =========================================================


def valid_evm_address(
    address: str,
) -> bool:
    return bool(
        re.fullmatch(
            r"0x[a-fA-F0-9]{40}",
            address,
        )
    )


def valid_solana_address(
    address: str,
) -> bool:
    return bool(
        re.fullmatch(
            r"[1-9A-HJ-NP-Za-km-z]{32,44}",
            address,
        )
    )


async def discover_token(
    chain: str,
    contract_address: str,
):
    config = SUPPORTED_CHAINS.get(
        chain
    )

    if not config:
        return None

    url = (
        "https://api.dexscreener.com/"
        f"latest/dex/tokens/{contract_address}"
    )

    try:
        async with httpx.AsyncClient(
            timeout=15
        ) as client:
            response = await client.get(
                url
            )

            response.raise_for_status()

            data = response.json()

    except Exception:
        return None

    pairs = data.get("pairs") or []

    matching = [
        pair
        for pair in pairs
        if pair.get("chainId")
        == config["dex_chain"]
    ]

    if not matching:
        return None

    best = max(
        matching,
        key=lambda pair: (
            pair.get("liquidity", {})
            .get("usd")
            or 0
        ),
    )

    base = best.get(
        "baseToken"
    ) or {}

    return {
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "pair_address": best.get(
            "pairAddress"
        ),
        "dex_url": best.get("url"),
    }


# =========================================================
# ADD TOKEN
# =========================================================


async def add_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🟡 BNB",
                    callback_data="bb_add_bnb",
                ),
                InlineKeyboardButton(
                    "🔷 Ethereum",
                    callback_data="bb_add_ethereum",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🟣 Solana",
                    callback_data="bb_add_solana",
                ),
                InlineKeyboardButton(
                    "🔴 Robinhood",
                    callback_data="bb_add_robinhood",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="bb_add_cancel",
                ),
            ],
        ]
    )

    await update.message.reply_text(
        "➕ *Add BuyBot Token*\n\n"
        "Select the blockchain:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    return SELECT_TOKEN_CHAIN


async def add_chain_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if query.data == "bb_add_cancel":
        await query.edit_message_text(
            "❌ Token addition cancelled."
        )

        return ConversationHandler.END

    chain = query.data.replace(
        "bb_add_",
        "",
    )

    if chain not in SUPPORTED_CHAINS:
        await query.edit_message_text(
            "❌ Unsupported chain."
        )

        return ConversationHandler.END

    context.user_data[
        "buybot_add_chain"
    ] = chain

    await query.edit_message_text(
        f"➕ *{SUPPORTED_CHAINS[chain]['name']}*\n\n"
        "Send the token contract/mint address.",
        parse_mode="Markdown",
    )

    return ENTER_TOKEN_ADDRESS


async def add_token_address_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    chain = context.user_data.get(
        "buybot_add_chain"
    )

    address = (
        update.message.text.strip()
        if update.message
        else ""
    )

    if chain in (
        "bnb",
        "ethereum",
        "robinhood",
    ):
        valid = valid_evm_address(
            address
        )
    elif chain == "solana":
        valid = valid_solana_address(
            address
        )
    else:
        valid = False

    if not valid:
        await update.message.reply_text(
            "❌ Invalid address format.\n\n"
            "Please send the correct token "
            "contract/mint address."
        )

        return ENTER_TOKEN_ADDRESS

    chat = update.effective_chat

    await ensure_settings(
        chat.id
    )

    discovered = await discover_token(
        chain,
        address,
    )

    pool = await get_pool()

    async with pool.acquire() as connection:

        existing = await connection.fetchrow(
            """
            SELECT id, enabled
            FROM buybot_tokens
            WHERE group_id = $1
              AND chain = $2
              AND LOWER(contract_address)
                    = LOWER($3);
            """,
            chat.id,
            chain,
            address,
        )

        if existing:
            await connection.execute(
                """
                UPDATE buybot_tokens
                SET
                    enabled = TRUE,
                    updated_at = NOW()
                WHERE id = $1;
                """,
                existing["id"],
            )

            await update.message.reply_text(
                "♻️ This token already exists.\n\n"
                "It has been enabled again."
            )

            context.user_data.pop(
                "buybot_add_chain",
                None,
            )

            return ConversationHandler.END

        token_name = (
            discovered.get("name")
            if discovered
            else None
        )

        token_symbol = (
            discovered.get("symbol")
            if discovered
            else None
        )

        pair_address = (
            discovered.get(
                "pair_address"
            )
            if discovered
            else None
        )

        dex_url = (
            discovered.get(
                "dex_url"
            )
            if discovered
            else None
        )

        await connection.execute(
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
            );
            """,
            chat.id,
            chain,
            address,
            token_name,
            token_symbol,
            pair_address,
            dex_url,
        )

    context.user_data.pop(
        "buybot_add_chain",
        None,
    )

    if discovered:
        await update.message.reply_text(
            "✅ *Token added to BuyBot!*\n\n"
            f"🪙 {token_name or 'Unknown'} "
            f"({token_symbol or '?'})\n"
            f"⛓️ {SUPPORTED_CHAINS[chain]['name']}\n\n"
            "Buy detection is now enabled "
            "for this token.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "✅ *Token saved!*\n\n"
            f"⛓️ {SUPPORTED_CHAINS[chain]['name']}\n"
            f"Contract: `{address}`\n\n"
            "No live DEX pair was detected yet. "
            "The token can remain monitored while "
            "waiting for trading to become available.",
            parse_mode="Markdown",
        )

    return ConversationHandler.END


# =========================================================
# REMOVE TOKEN
# =========================================================


async def remove_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
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
            ORDER BY id DESC;
            """,
            chat.id,
        )

    if not rows:
        await update.message.reply_text(
            "📭 There are no active BuyBot tokens "
            "in this group."
        )

        return ConversationHandler.END

    buttons = []

    for row in rows:
        label = (
            f"{row['token_symbol'] or row['token_name'] or 'Token'} "
            f"• {row['chain'].upper()}"
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
        "➖ *Remove BuyBot Token*\n\n"
        "Select a token:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return SELECT_REMOVE_TOKEN


async def remove_token_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not await is_group_admin(update):
        await query.answer(
            "Admin access required.",
            show_alert=True,
        )
        return ConversationHandler.END

    if query.data == "bb_remove_cancel":
        await query.edit_message_text(
            "❌ Removal cancelled."
        )

        return ConversationHandler.END

    try:
        token_id = int(
            query.data.replace(
                "bb_remove_token_",
                "",
            )
        )
    except ValueError:
        await query.edit_message_text(
            "❌ Invalid token selection."
        )
        return ConversationHandler.END

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE buybot_tokens
            SET
                enabled = FALSE,
                updated_at = NOW()
            WHERE id = $1
              AND group_id = $2;
            """,
            token_id,
            chat.id,
        )

    await query.edit_message_text(
        "✅ Token removed from BuyBot monitoring."
    )

    return ConversationHandler.END


async def list_tokens(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                chain,
                contract_address,
                token_name,
                token_symbol,
                pair_address,
                enabled
            FROM buybot_tokens
            WHERE group_id = $1
            ORDER BY enabled DESC, id DESC;
            """,
            chat.id,
        )

    if not rows:
        await update.message.reply_text(
            "📭 No BuyBot tokens have been added yet.\n\n"
            "Use /add to add one."
        )
        return

    lines = [
        "🤖 *BUYBOT TOKENS*",
        "",
    ]

    for index, row in enumerate(
        rows,
        start=1,
    ):
        status = (
            "🟢"
            if row["enabled"]
            else "⚪"
        )

        name = (
            row["token_symbol"]
            or row["token_name"]
            or "Unknown"
        )

        pair = (
            "Pair detected"
            if row["pair_address"]
            else "Waiting for pair"
        )

        lines.append(
            f"{status} *{index}. {name}*"
        )

        lines.append(
            f"⛓️ {row['chain'].upper()}"
        )

        lines.append(
            f"🔎 {pair}"
        )

        lines.append(
            f"`{row['contract_address']}`"
        )

        lines.append("")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# =========================================================
# MEDIA
# =========================================================


async def media_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🖼 *BuyBot Media*\n\n"
        "Send a photo, GIF, or video in your next message.\n\n"
        "The uploaded media will be used for "
        "future BuyBot alerts.\n\n"
        "To remove current media, use:\n"
        "`/buybotresetmedia`",
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

    chat = update.effective_chat

    await ensure_settings(
        chat.id
    )

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                media_type = $1,
                media_id = $2,
                updated_at = NOW()
            WHERE group_id = $3;
            """,
            media_type,
            media_id,
            chat.id,
        )

    await message.reply_text(
        "✅ BuyBot media updated.\n\n"
        f"Type: `{media_type}`",
        parse_mode="Markdown",
    )

    return await show_buybot_settings(
        update,
        context,
    )


async def reset_media_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return

    chat = update.effective_chat

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                media_type = NULL,
                media_id = NULL,
                updated_at = NOW()
            WHERE group_id = $1;
            """,
            chat.id,
        )

    await update.message.reply_text(
        "✅ BuyBot media removed."
    )


# =========================================================
# TITLE
# =========================================================


async def title_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "✏️ *Custom Alert Title*\n\n"
        "Send the title you want BuyBot to use.\n\n"
        "Example:\n"
        "`🚨 Fresh Buy Spotted!`",
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
            "❌ Title is too long. Maximum 200 characters."
        )
        return ENTER_TITLE

    await ensure_settings(
        update.effective_chat.id
    )

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                alert_title = $1,
                updated_at = NOW()
            WHERE group_id = $2;
            """,
            title,
            update.effective_chat.id,
        )

    return await show_buybot_settings(
        update,
        context,
    )


# =========================================================
# TEMPLATE
# =========================================================


async def template_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "📝 *Custom BuyBot Template*\n\n"
        "Send the alert format you want.\n\n"
        "Available variables:\n\n"
        "`{token_name}`\n"
        "`{token_symbol}`\n"
        "`{chain}`\n"
        "`{spent_usd}`\n"
        "`{spent_native}`\n"
        "`{native_symbol}`\n"
        "`{received_amount}`\n"
        "`{received_symbol}`\n"
        "`{market_cap}`\n"
        "`{buyer}`\n"
        "`{holder_status}`\n"
        "`{buy_emoji}`\n"
        "`{holder_emoji}`\n"
        "`{market_cap_emoji}`\n"
        "`{spent_emoji}`\n"
        "`{received_emoji}`\n"
        "`{network_emoji}`\n\n"
        "Example:\n\n"
        "`{buy_emoji} {token_name} ({token_symbol})`\n"
        "`{spent_emoji} Spent: ${spent_usd}`\n"
        "`{received_emoji} Got: {received_amount} {received_symbol}`\n"
        "`{market_cap_emoji} MC: ${market_cap}`\n"
        "`{network_emoji} {chain}`",
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
            "❌ Template is too long. Maximum 3000 characters."
        )
        return ENTER_TEMPLATE

    await ensure_settings(
        update.effective_chat.id
    )

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                alert_template = $1,
                updated_at = NOW()
            WHERE group_id = $2;
            """,
            template,
            update.effective_chat.id,
        )

    return await show_buybot_settings(
        update,
        context,
    )


# =========================================================
# EMOJIS
# =========================================================


async def emojis_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "😀 *BuyBot Emojis*\n\n"
        "Choose which BuyBot element you want "
        "to customize:",
        parse_mode="Markdown",
        reply_markup=emoji_keyboard(),
    )

    return BUYBOT_SETTINGS


async def emoji_type_selected(
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
        "bb_emoji_market": (
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

    selected = mapping.get(
        query.data
    )

    if not selected:
        return BUYBOT_SETTINGS

    field, label = selected

    context.user_data[
        "buybot_emoji_field"
    ] = field

    await query.edit_message_text(
        f"😀 *{label} Emoji*\n\n"
        "Send the emoji/custom emoji you want to use.",
        parse_mode="Markdown",
    )

    return ENTER_EMOJI


async def emoji_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    field = context.user_data.get(
        "buybot_emoji_field"
    )

    if not field:
        return BUYBOT_SETTINGS

    value = update.message.text.strip()

    if len(value) > 100:
        await update.message.reply_text(
            "❌ Emoji value is too long."
        )
        return ENTER_EMOJI

    allowed_fields = {
        "buy_emoji",
        "new_holder_emoji",
        "market_cap_emoji",
        "spent_emoji",
        "received_emoji",
        "network_emoji",
    }

    if field not in allowed_fields:
        return BUYBOT_SETTINGS

    await ensure_settings(
        update.effective_chat.id
    )

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            f"""
            UPDATE buybot_settings
            SET
                {field} = $1,
                updated_at = NOW()
            WHERE group_id = $2;
            """,
            value,
            update.effective_chat.id,
        )

    context.user_data.pop(
        "buybot_emoji_field",
        None,
    )

    return await show_buybot_settings(
        update,
        context,
    )


# =========================================================
# MINIMUM BUY
# =========================================================


async def min_buy_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "💰 *Minimum Buy Amount*\n\n"
        "Send the minimum USD value that should "
        "trigger a BuyBot alert.\n\n"
        "Examples:\n"
        "`0` = show every detected buy\n"
        "`10` = show buys of $10+\n"
        "`50` = show buys of $50+",
        parse_mode="Markdown",
    )

    return ENTER_MIN_BUY


async def min_buy_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(update):
        return ConversationHandler.END

    raw = update.message.text.strip()

    try:
        value = float(raw)

        if value < 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "❌ Please send a valid positive number."
        )
        return ENTER_MIN_BUY

    await ensure_settings(
        update.effective_chat.id
    )

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                min_buy_usd = $1,
                updated_at = NOW()
            WHERE group_id = $2;
            """,
            value,
            update.effective_chat.id,
        )

    return await show_buybot_settings(
        update,
        context,
    )


# =========================================================
# CUSTOM BUTTONS
# =========================================================


async def buttons_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    chat_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                id,
                button_name,
                button_url,
                position
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC;
            """,
            chat_id,
        )

    lines = [
        "🔘 *Custom BuyBot Buttons*",
        "",
    ]

    if rows:
        for index, row in enumerate(
            rows,
            start=1,
        ):
            lines.append(
                f"{index}. `{row['button_name']}`"
            )

        lines.append("")

    lines.append(
        f"Buttons used: {len(rows)}/3"
    )

    keyboard = []

    if len(rows) < 3:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "➕ Add Button",
                    callback_data="bb_button_add",
                )
            ]
        )

    if rows:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🗑 Remove All",
                    callback_data="bb_button_clear",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="bb_settings",
            )
        ]
    )

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return BUYBOT_SETTINGS


async def button_add_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    chat_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as connection:
        count = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_buttons
            WHERE group_id = $1;
            """,
            chat_id,
        )

    if count >= 3:
        await query.answer(
            "Maximum 3 custom buttons.",
            show_alert=True,
        )
        return BUYBOT_SETTINGS

    await query.edit_message_text(
        "➕ *Add Custom Button*\n\n"
        "Send it in this format:\n\n"
        "`Button Name | Button Link`\n\n"
        "Example:\n"
        "`Website | https://example.com`\n\n"
        "Maximum 3 custom buttons.",
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
            "`Button Name | Button Link`",
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
            "❌ Both button name and link are required."
        )
        return ENTER_BUTTON

    if not re.match(
        r"^https?://",
        url,
        re.IGNORECASE,
    ):
        await update.message.reply_text(
            "❌ Button link must start with "
            "`http://` or `https://`.",
            parse_mode="Markdown",
        )
        return ENTER_BUTTON

    if len(name) > 64:
        await update.message.reply_text(
            "❌ Button name is too long."
        )
        return ENTER_BUTTON

    chat_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as connection:
        count = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_buttons
            WHERE group_id = $1;
            """,
            chat_id,
        )

        if count >= 3:
            await update.message.reply_text(
                "❌ Maximum 3 custom buttons allowed."
            )
            return BUYBOT_SETTINGS

        position = int(count)

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
            );
            """,
            chat_id,
            name,
            url,
            position,
        )

    return await show_buybot_settings(
        update,
        context,
    )


async def clear_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not await is_group_admin(update):
        return BUYBOT_SETTINGS

    chat_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            DELETE FROM buybot_buttons
            WHERE group_id = $1;
            """,
            chat_id,
        )

    await query.edit_message_text(
        "✅ All custom BuyBot buttons removed.",
        reply_markup=settings_keyboard(),
    )

    return BUYBOT_SETTINGS


# =========================================================
# PREVIEW
# =========================================================


def render_preview(
    settings,
):
    title = (
        settings["alert_title"]
        or "🚀 Buy Detected"
    )

    template = (
        settings["alert_template"]
        or
        "{buy_emoji} {token_name} "
        "({token_symbol})\n\n"
        "{spent_emoji} Spent: ${spent_usd}\n"
        "{received_emoji} Received: "
        "{received_amount} {received_symbol}\n\n"
        "{holder_emoji} New Holder\n"
        "{market_cap_emoji} Market Cap: ${market_cap}\n"
        "{network_emoji} {chain}"
    )

    values = {
        "token_name": "Example Token",
        "token_symbol": "EXM",
        "chain": "BNB",
        "spent_usd": "125.40",
        "spent_native": "0.21",
        "native_symbol": "BNB",
        "received_amount": "12,450",
        "received_symbol": "EXM",
        "market_cap": "84,500",
        "buyer": "0x1234...5678",
        "holder_status": "New Holder",
        "buy_emoji": (
            settings["buy_emoji"]
            or "🟢"
        ),
        "holder_emoji": (
            settings["new_holder_emoji"]
            or "👤"
        ),
        "market_cap_emoji": (
            settings["market_cap_emoji"]
            or "💎"
        ),
        "spent_emoji": (
            settings["spent_emoji"]
            or "💸"
        ),
        "received_emoji": (
            settings["received_emoji"]
            or "🪙"
        ),
        "network_emoji": (
            settings["network_emoji"]
            or "⛓️"
        ),
    }

    try:
        body = template.format(
            **values
        )
    except Exception:
        body = (
            "🟢 Buy Detected\n\n"
            "Example Token (EXM)\n"
            "💸 Spent: $125.40\n"
            "🪙 Received: 12,450 EXM\n"
            "💎 Market Cap: $84,500\n"
            "⛓️ BNB"
        )

    return (
        f"*{title}*\n\n"
        f"{body}"
    )


async def preview_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not await is_group_admin(update):
        return BUYBOT_SETTINGS

    settings = await get_settings(
        update.effective_chat.id
    )

    if not settings:
        await query.edit_message_text(
            "❌ BuyBot settings are not initialized."
        )
        return BUYBOT_SETTINGS

    preview = render_preview(
        settings
    )

    await query.edit_message_text(
        "👀 *BUYBOT PREVIEW*\n\n"
        + preview,
        parse_mode="Markdown",
        reply_markup=settings_keyboard(),
    )

    return BUYBOT_SETTINGS


# =========================================================
# RESET
# =========================================================


async def reset_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not await is_group_admin(update):
        return BUYBOT_SETTINGS

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Yes, Reset",
                    callback_data="bb_reset_confirm",
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="bb_settings",
                ),
            ]
        ]
    )

    await query.edit_message_text(
        "♻️ *Reset BuyBot Settings?*\n\n"
        "This will reset the appearance settings "
        "and remove custom buttons.\n\n"
        "Your monitored tokens will NOT be removed.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    return BUYBOT_SETTINGS


async def reset_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not await is_group_admin(update):
        return BUYBOT_SETTINGS

    chat_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE buybot_settings
            SET
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
            WHERE group_id = $1;
            """,
            chat_id,
        )

        await connection.execute(
            """
            DELETE FROM buybot_buttons
            WHERE group_id = $1;
            """,
            chat_id,
        )

    await query.edit_message_text(
        "✅ BuyBot appearance has been reset.\n\n"
        "Your monitored tokens are still safe.",
        reply_markup=settings_keyboard(),
    )

    return BUYBOT_SETTINGS


# =========================================================
# SETTINGS CALLBACK ROUTER
# =========================================================


async def settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not await is_group_admin(update):
        await query.answer(
            "Admin access required.",
            show_alert=True,
        )
        return BUYBOT_SETTINGS

    data = query.data

    if data == "bb_settings":
        await query.answer()

        return await show_buybot_settings(
            update,
            context,
        )

    if data == "bb_close":
        await query.answer()

        await query.edit_message_text(
            "✅ BuyBot settings closed."
        )

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
        return await emoji_type_selected(
            update,
            context,
        )

    if data == "bb_minbuy":
        return await min_buy_menu(
            update,
            context,
        )

    if data == "bb_buttons":
        return await buttons_menu(
            update,
            context,
        )

    if data == "bb_button_add":
        return await button_add_start(
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

    return BUYBOT_SETTINGS


# =========================================================
# CONVERSATION BUILDER
# =========================================================


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
        ],

        states={

            # ---------------------------------------------
            # MAIN SETTINGS MENU
            # ---------------------------------------------

            BUYBOT_SETTINGS: [
                CallbackQueryHandler(
                    settings_callback,
                    pattern=r"^bb_(settings|close|media|title|template|emojis|minbuy|buttons|button_add|button_clear|preview|reset|reset_confirm|emoji_.*)$",
                ),
            ],

            # ---------------------------------------------
            # ADD TOKEN
            # ---------------------------------------------

            SELECT_TOKEN_CHAIN: [
                CallbackQueryHandler(
                    add_chain_selected,
                    pattern=r"^bb_add_",
                ),
            ],

            ENTER_TOKEN_ADDRESS: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    add_token_address_received,
                ),
            ],

            # ---------------------------------------------
            # REMOVE TOKEN
            # ---------------------------------------------

            SELECT_REMOVE_TOKEN: [
                CallbackQueryHandler(
                    remove_token_selected,
                    pattern=r"^bb_remove_(token_|cancel)",
                ),
            ],

            # ---------------------------------------------
            # CUSTOM BUTTON
            # ---------------------------------------------

            ENTER_BUTTON: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    button_received,
                ),
            ],

            # ---------------------------------------------
            # TITLE
            # ---------------------------------------------

            ENTER_TITLE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    title_received,
                ),
            ],

            # ---------------------------------------------
            # TEMPLATE
            # ---------------------------------------------

            ENTER_TEMPLATE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    template_received,
                ),
            ],

            # ---------------------------------------------
            # EMOJI
            # ---------------------------------------------

            ENTER_EMOJI: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    emoji_received,
                ),
            ],

            # ---------------------------------------------
            # MINIMUM BUY
            # ---------------------------------------------

            ENTER_MIN_BUY: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    min_buy_received,
                ),
            ],

            # ---------------------------------------------
            # MEDIA
            # ---------------------------------------------

            ENTER_MEDIA: [
                MessageHandler(
                    (
                        filters.PHOTO
                        | filters.VIDEO
                        | filters.ANIMATION
                    ),
                    media_received,
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


# =========================================================
# CANCEL
# =========================================================


async def cancel_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop(
        "buybot_add_chain",
        None,
    )

    context.user_data.pop(
        "buybot_waiting_for",
        None,
    )

    context.user_data.pop(
        "buybot_emoji_field",
        None,
    )

    await update.message.reply_text(
        "❌ BuyBot operation cancelled."
    )

    return ConversationHandler.END
