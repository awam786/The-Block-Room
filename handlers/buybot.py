from urllib.parse import urlparse
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
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_IDS

from database.connection import get_pool


MAX_CUSTOM_BUTTONS = 3


(
    BUYBOT_MENU,
    BUYBOT_BUTTONS,
    BUYBOT_ADD_BUTTON,
    BUYBOT_ADD_CHAIN,
    BUYBOT_ADD_ADDRESS,
    BUYBOT_REMOVE_ADDRESS,
) = range(6)


SUPPORTED_CHAINS = {
    "bnb": {
        "name": "BNB Smart Chain",
        "symbol": "BNB",
        "dex_chain": "bsc",
    },
    "ethereum": {
        "name": "Ethereum",
        "symbol": "ETH",
        "dex_chain": "ethereum",
    },
    "solana": {
        "name": "Solana",
        "symbol": "SOL",
        "dex_chain": "solana",
    },
    "robinhood": {
        "name": "Robinhood Chain",
        "symbol": "ETH",
        "dex_chain": "robinhood",
    },
}


DEX_BASE_URL = (
    "https://api.dexscreener.com"
)


def is_main_admin(
    user_id: int,
) -> bool:
    return user_id in ADMIN_IDS


def normalize_chain(
    value: str,
):
    value = (
        value
        .lower()
        .strip()
    )

    aliases = {
        "bnb": "bnb",
        "bsc": "bnb",
        "binance": "bnb",
        "binance smart chain": "bnb",

        "eth": "ethereum",
        "ethereum": "ethereum",

        "sol": "solana",
        "solana": "solana",

        "robinhood": "robinhood",
        "robinhood chain": "robinhood",
        "rh": "robinhood",
    }

    return aliases.get(
        value
    )


def valid_evm_address(
    value: str,
) -> bool:
    return bool(
        re.fullmatch(
            r"0x[a-fA-F0-9]{40}",
            value,
        )
    )


def valid_solana_address(
    value: str,
) -> bool:
    if not re.fullmatch(
        r"[1-9A-HJ-NP-Za-km-z]+",
        value,
    ):
        return False

    return 32 <= len(value) <= 44


def valid_contract_address(
    chain: str,
    address: str,
) -> bool:

    address = address.strip()

    if chain in {
        "bnb",
        "ethereum",
        "robinhood",
    }:
        return valid_evm_address(
            address
        )

    if chain == "solana":
        return valid_solana_address(
            address
        )

    return False


async def is_group_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return False

    if chat.type not in {
        "group",
        "supergroup",
    }:
        return False

    try:
        member = (
            await context.bot.get_chat_member(
                chat.id,
                user.id,
            )
        )

    except Exception:
        return False

    return member.status in {
        "administrator",
        "creator",
    }


async def ensure_group(
    update: Update,
):
    chat = update.effective_chat

    if not chat:
        return

    pool = await get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO groups (
                telegram_id,
                title,
                username,
                type,
                is_active,
                updated_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                TRUE,
                NOW()
            )
            ON CONFLICT (
                telegram_id
            )
            DO UPDATE SET
                title = EXCLUDED.title,
                username = EXCLUDED.username,
                type = EXCLUDED.type,
                is_active = TRUE,
                updated_at = NOW()
            """,
            chat.id,
            chat.title,
            chat.username,
            chat.type,
        )


async def get_buybot_status(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT
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
            WHERE group_id = $1
            """,
            group_id,
        )

    if not row:
        return None

    return dict(row)


async def get_buttons(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT
                id,
                position,
                button_name,
                button_url
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC
            """,
            group_id,
        )

    return rows


async def is_buybot_enabled(
    group_id: int,
) -> bool:

    pool = await get_pool()

    async with pool.acquire() as conn:

        result = await conn.fetchval(
            """
            SELECT enabled
            FROM buybot_settings
            WHERE group_id = $1
            """,
            group_id,
        )

    return bool(result)


# ============================================================
# DEX SCREENER TOKEN DISCOVERY
# ============================================================

async def discover_token(
    chain: str,
    contract_address: str,
):
    """
    Discover token metadata and the best available
    DEX pair.

    If the token is pre-launch or not indexed yet,
    this returns an empty result instead of failing.

    This allows the token to remain stored for later
    discovery by the monitoring layer.
    """

    chain_config = (
        SUPPORTED_CHAINS.get(chain)
    )

    if not chain_config:
        return None

    url = (
        f"{DEX_BASE_URL}/latest/dex/tokens/"
        f"{contract_address}"
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

    except (
        httpx.HTTPError,
        ValueError,
    ):

        return None

    pairs = data.get(
        "pairs"
    ) or []

    dex_chain = chain_config[
        "dex_chain"
    ]

    matching_pairs = []

    for pair in pairs:

        pair_chain = str(
            pair.get(
                "chainId",
                "",
            )
        ).lower()

        if pair_chain != dex_chain:
            continue

        matching_pairs.append(
            pair
        )

    if not matching_pairs:
        return None

    def liquidity_value(
        pair,
    ):
        liquidity = (
            pair.get(
                "liquidity"
            )
            or {}
        )

        try:
            return float(
                liquidity.get(
                    "usd"
                )
                or 0
            )
        except (
            ValueError,
            TypeError,
        ):
            return 0.0

    best_pair = max(
        matching_pairs,
        key=liquidity_value,
    )

    base_token = (
        best_pair.get(
            "baseToken"
        )
        or {}
    )

    return {
        "token_name": (
            base_token.get(
                "name"
            )
        ),
        "token_symbol": (
            base_token.get(
                "symbol"
            )
        ),
        "token_address": (
            base_token.get(
                "address"
            )
        ),
        "pair_address": (
            best_pair.get(
                "pairAddress"
            )
        ),
        "dex_url": (
            best_pair.get(
                "url"
            )
        ),
    }


# ============================================================
# MAIN ADMIN BUYBOT ACTIVATION
# ============================================================

async def activate_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user or not is_main_admin(
        user.id
    ):

        if update.message:
            await update.message.reply_text(
                "⛔ Only the main bot administrator "
                "can activate BuyBot for a group."
            )

        return

    chat = update.effective_chat

    target_group_id = None

    if context.args:

        try:
            target_group_id = int(
                context.args[0]
            )

        except ValueError:
            target_group_id = None

    if chat and chat.type in {
        "group",
        "supergroup",
    }:
        target_group_id = chat.id

    if target_group_id is None:

        await update.message.reply_text(
            "Usage:\n\n"
            "/activebuybot <group_id>\n\n"
            "Or run /activebuybot directly "
            "inside the target group."
        )

        return

    try:

        target_chat = (
            await context.bot.get_chat(
                target_group_id
            )
        )

    except Exception:

        await update.message.reply_text(
            "❌ I could not access that group.\n\n"
            "Make sure the bot has been added to "
            "the group first."
        )

        return

    pool = await get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO groups (
                telegram_id,
                title,
                username,
                type,
                is_active,
                updated_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                TRUE,
                NOW()
            )
            ON CONFLICT (
                telegram_id
            )
            DO UPDATE SET
                title = EXCLUDED.title,
                username = EXCLUDED.username,
                type = EXCLUDED.type,
                is_active = TRUE,
                updated_at = NOW()
            """,
            target_chat.id,
            target_chat.title,
            target_chat.username,
            target_chat.type,
        )

        await conn.execute(
            """
            INSERT INTO buybot_settings (
                group_id,
                enabled
            )
            VALUES (
                $1,
                TRUE
            )
            ON CONFLICT (
                group_id
            )
            DO UPDATE SET
                enabled = TRUE,
                updated_at = NOW()
            """,
            target_chat.id,
        )

    await update.message.reply_text(
        "✅ *BUYBOT ACTIVATED*\n\n"
        f"Group: {target_chat.title or 'Unknown'}\n"
        f"Group ID: `{target_chat.id}`\n\n"
        "Group administrators can now use:\n\n"
        "🤖 /buybot\n"
        "➕ /add\n"
        "➖ /remove\n"
        "📋 /tokens",
        parse_mode="Markdown",
    )


async def remove_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user or not is_main_admin(
        user.id
    ):

        if update.message:
            await update.message.reply_text(
                "⛔ Only the main bot administrator "
                "can disable BuyBot."
            )

        return

    chat = update.effective_chat

    target_group_id = None

    if context.args:

        try:
            target_group_id = int(
                context.args[0]
            )

        except ValueError:
            target_group_id = None

    if chat and chat.type in {
        "group",
        "supergroup",
    }:
        target_group_id = chat.id

    if target_group_id is None:

        await update.message.reply_text(
            "Usage:\n\n"
            "/removebuybot <group_id>\n\n"
            "Or run it inside the target group."
        )

        return

    pool = await get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE buybot_settings
            SET
                enabled = FALSE,
                updated_at = NOW()
            WHERE group_id = $1
            """,
            target_group_id,
        )

        await conn.execute(
            """
            UPDATE groups
            SET
                is_active = TRUE,
                updated_at = NOW()
            WHERE telegram_id = $1
            """,
            target_group_id,
        )

    await update.message.reply_text(
        "🔴 *BUYBOT DISABLED*\n\n"
        f"Group ID: `{target_group_id}`",
        parse_mode="Markdown",
    )


# ============================================================
# BUYBOT MAIN MENU
# ============================================================

async def buybot_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat = update.effective_chat

    if not chat or chat.type not in {
        "group",
        "supergroup",
    }:

        await update.message.reply_text(
            "👥 Use /buybot inside the group "
            "where BuyBot is active."
        )

        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await update.message.reply_text(
            "⛔ Only group administrators "
            "can manage BuyBot settings."
        )

        return ConversationHandler.END

    await ensure_group(
        update
    )

    enabled = await is_buybot_enabled(
        chat.id
    )

    if not enabled:

        await update.message.reply_text(
            "🔴 BuyBot is not active in this group.\n\n"
            "Ask the bot owner to activate it."
        )

        return ConversationHandler.END

    await send_buybot_menu(
        update,
        context,
    )

    return BUYBOT_MENU


async def send_buybot_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat = update.effective_chat

    if not chat:
        return

    buttons = await get_buttons(
        chat.id
    )

    status = await get_buybot_status(
        chat.id
    )

    button_count = len(
        buttons
    )

    if status:

        enabled_text = (
            "🟢 Active"
            if status["enabled"]
            else "🔴 Disabled"
        )

    else:
        enabled_text = "🔴 Disabled"

    text = (
        "🤖 *THE BLOCK ROOM BUYBOT*\n\n"
        f"Status: {enabled_text}\n\n"
        "Manage how BuyBot alerts appear "
        "in this group.\n\n"
        "🔗 *Custom inline buttons:* "
        f"{button_count}/{MAX_CUSTOM_BUTTONS}\n\n"
        "📋 Use /tokens to see monitored tokens.\n"
        "➕ Use /add to monitor a token.\n"
        "➖ Use /remove to stop monitoring a token.\n\n"
        "Choose what you want to configure:"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🔗 Inline Buttons",
                callback_data="buybot_buttons",
            ),
        ],
        [
            InlineKeyboardButton(
                "📋 Preview Alert",
                callback_data="buybot_preview",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎨 Appearance",
                callback_data="buybot_appearance",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Close",
                callback_data="buybot_close",
            ),
        ],
    ]

    markup = InlineKeyboardMarkup(
        keyboard
    )

    if update.callback_query:

        await update.callback_query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )

    elif update.message:

        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )


# ============================================================
# TOKEN MANAGEMENT
# ============================================================

async def add_token_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat = update.effective_chat

    if not chat or chat.type not in {
        "group",
        "supergroup",
    }:

        await update.message.reply_text(
            "👥 Use /add inside the BuyBot group."
        )

        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await update.message.reply_text(
            "⛔ Only group administrators "
            "can add monitored tokens."
        )

        return ConversationHandler.END

    if not await is_buybot_enabled(
        chat.id
    ):

        await update.message.reply_text(
            "🔴 BuyBot is not active in this group."
        )

        return ConversationHandler.END

    keyboard = [
        [
            InlineKeyboardButton(
                "🟡 BNB",
                callback_data=(
                    "buybot_add_chain_bnb"
                ),
            ),
            InlineKeyboardButton(
                "🔵 Ethereum",
                callback_data=(
                    "buybot_add_chain_ethereum"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                "🟣 Solana",
                callback_data=(
                    "buybot_add_chain_solana"
                ),
            ),
            InlineKeyboardButton(
                "🔴 Robinhood",
                callback_data=(
                    "buybot_add_chain_robinhood"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=(
                    "buybot_token_cancel"
                ),
            ),
        ],
    ]

    await update.message.reply_text(
        "➕ *ADD MONITORED TOKEN*\n\n"
        "Choose the blockchain where the token "
        "is deployed:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="Markdown",
    )

    return BUYBOT_ADD_CHAIN


async def add_token_chain_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await query.answer(
            "Only group administrators can do this.",
            show_alert=True,
        )

        return BUYBOT_ADD_CHAIN

    chain = query.data.replace(
        "buybot_add_chain_",
        "",
    )

    if chain not in SUPPORTED_CHAINS:

        await query.answer(
            "Unsupported chain.",
            show_alert=True,
        )

        return BUYBOT_ADD_CHAIN

    context.user_data[
        "buybot_add_chain"
    ] = chain

    chain_name = (
        SUPPORTED_CHAINS[
            chain
        ]["name"]
    )

    await query.edit_message_text(
        "➕ *TOKEN CONTRACT*\n\n"
        f"Network: *{chain_name}*\n\n"
        "Send the token contract address "
        "in your next message.\n\n"
        "Example:\n"
        "`0x1234...` for EVM chains\n"
        "or the Solana mint address.",
        parse_mode="Markdown",
    )

    return BUYBOT_ADD_ADDRESS


async def add_token_address_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await update.message.reply_text(
            "⛔ Only group administrators "
            "can add tokens."
        )

        return ConversationHandler.END

    chain = context.user_data.get(
        "buybot_add_chain"
    )

    if not chain:

        await update.message.reply_text(
            "❌ Session expired. Please use /add again."
        )

        return ConversationHandler.END

    address = (
        update.message.text or ""
    ).strip()

    if not valid_contract_address(
        chain,
        address,
    ):

        chain_name = (
            SUPPORTED_CHAINS[
                chain
            ]["name"]
        )

        await update.message.reply_text(
            "❌ Invalid contract address.\n\n"
            f"Selected network: {chain_name}\n\n"
            "Please send a valid token contract "
            "address."
        )

        return BUYBOT_ADD_ADDRESS

    await update.message.reply_text(
        "🔎 Checking the token and looking for "
        "a live trading pair..."
    )

    discovery = await discover_token(
        chain,
        address,
    )

    token_name = None
    token_symbol = None
    pair_address = None
    dex_url = None

    if discovery:

        discovered_address = (
            discovery.get(
                "token_address"
            )
        )

        if (
            discovered_address
            and chain != "solana"
            and discovered_address.lower()
            != address.lower()
        ):

            discovery = None

        elif (
            discovered_address
            and chain == "solana"
            and discovered_address
            != address
        ):

            discovery = None

    if discovery:

        token_name = (
            discovery.get(
                "token_name"
            )
        )

        token_symbol = (
            discovery.get(
                "token_symbol"
            )
        )

        pair_address = (
            discovery.get(
                "pair_address"
            )
        )

        dex_url = (
            discovery.get(
                "dex_url"
            )
        )

    pool = await get_pool()

    async with pool.acquire() as conn:

        existing = await conn.fetchrow(
            """
            SELECT
                id,
                token_name,
                token_symbol,
                pair_address,
                dex_url,
                enabled
            FROM buybot_tokens
            WHERE group_id = $1
              AND chain = $2
              AND LOWER(contract_address) =
                  LOWER($3)
            """,
            chat.id,
            chain,
            address,
        )

        if existing:

            if not existing["enabled"]:

                await conn.execute(
                    """
                    UPDATE buybot_tokens
                    SET
                        enabled = TRUE,
                        token_name = COALESCE(
                            $1,
                            token_name
                        ),
                        token_symbol = COALESCE(
                            $2,
                            token_symbol
                        ),
                        pair_address = COALESCE(
                            $3,
                            pair_address
                        ),
                        dex_url = COALESCE(
                            $4,
                            dex_url
                        ),
                        updated_at = NOW()
                    WHERE id = $5
                    """,
                    token_name,
                    token_symbol,
                    pair_address,
                    dex_url,
                    existing["id"],
                )

            else:

                await update.message.reply_text(
                    "⚠️ This token is already being "
                    "monitored in this group."
                )

                context.user_data.pop(
                    "buybot_add_chain",
                    None,
                )

                return ConversationHandler.END

        else:

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
                token_name,
                token_symbol,
                pair_address,
                dex_url,
            )

    chain_name = (
        SUPPORTED_CHAINS[
            chain
        ]["name"]
    )

    context.user_data.pop(
        "buybot_add_chain",
        None,
    )

    if pair_address:

        await update.message.reply_text(
            "✅ *TOKEN ADDED*\n\n"
            f"🪙 Token: "
            f"*{token_name or 'Unknown'}*"
            f" (`{token_symbol or '?'} `)\n"
            f"⛓️ Network: *{chain_name}*\n"
            f"📍 Contract:\n`{address}`\n\n"
            f"🔗 Pair:\n`{pair_address}`\n\n"
            "🟢 Live pair detected.\n"
            "BuyBot is ready to monitor swaps.",
            parse_mode="Markdown",
        )

    else:

        await update.message.reply_text(
            "✅ *TOKEN SAVED*\n\n"
            f"⛓️ Network: *{chain_name}*\n"
            f"📍 Contract:\n`{address}`\n\n"
            "🟡 No live trading pair was found yet.\n\n"
            "The token remains saved and can be "
            "picked up when a trading pair becomes "
            "available.",
            parse_mode="Markdown",
        )

    return ConversationHandler.END


async def list_tokens(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat = update.effective_chat

    if not chat or chat.type not in {
        "group",
        "supergroup",
    }:

        await update.message.reply_text(
            "👥 Use /tokens inside the BuyBot group."
        )

        return

    if not await is_group_admin(
        update,
        context,
    ):

        await update.message.reply_text(
            "⛔ Only group administrators "
            "can view BuyBot token settings."
        )

        return

    pool = await get_pool()

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT
                id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                pair_address,
                enabled
            FROM buybot_tokens
            WHERE group_id = $1
            ORDER BY created_at ASC
            """,
            chat.id,
        )

    if not rows:

        await update.message.reply_text(
            "📋 *MONITORED TOKENS*\n\n"
            "No tokens are being monitored yet.\n\n"
            "Use /add to add the first token.",
            parse_mode="Markdown",
        )

        return

    lines = [
        "📋 *MONITORED TOKENS*",
        "",
    ]

    for index, row in enumerate(
        rows,
        start=1,
    ):

        chain_name = (
            SUPPORTED_CHAINS.get(
                row["chain"],
                {},
            ).get(
                "name",
                row["chain"],
            )
        )

        status = (
            "🟢"
            if row["enabled"]
            else "🔴"
        )

        name = (
            row["token_name"]
            or "Unknown Token"
        )

        symbol = (
            row["token_symbol"]
            or "?"
        )

        lines.append(
            f"{index}. {status} *{name}* "
            f"(`{symbol}`)"
        )

        lines.append(
            f"   ⛓️ {chain_name}"
        )

        lines.append(
            f"   `{row['contract_address']}`"
        )

        if row["pair_address"]:

            lines.append(
                f"   🔗 Pair: "
                f"`{row['pair_address']}`"
            )

        else:

            lines.append(
                "   🟡 Pair: Not detected yet"
            )

        lines.append("")

    lines.append(
        "➕ /add — Add token"
    )

    lines.append(
        "➖ /remove — Remove token"
    )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


async def remove_token_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat = update.effective_chat

    if not chat or chat.type not in {
        "group",
        "supergroup",
    }:

        await update.message.reply_text(
            "👥 Use /remove inside the BuyBot group."
        )

        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await update.message.reply_text(
            "⛔ Only group administrators "
            "can remove monitored tokens."
        )

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
              AND enabled = TRUE
            ORDER BY created_at ASC
            """,
            chat.id,
        )

    if not rows:

        await update.message.reply_text(
            "📋 There are no active monitored tokens."
        )

        return ConversationHandler.END

    keyboard = []

    for row in rows:

        name = (
            row["token_symbol"]
            or row["token_name"]
            or "Token"
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"❌ {name} · "
                    f"{row['chain'].upper()}",
                    callback_data=(
                        f"buybot_remove_token_"
                        f"{row['id']}"
                    ),
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "Cancel",
                callback_data=(
                    "buybot_token_cancel"
                ),
            )
        ]
    )

    await update.message.reply_text(
        "➖ *REMOVE MONITORED TOKEN*\n\n"
        "Choose the token you want to stop "
        "monitoring:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="Markdown",
    )

    return BUYBOT_REMOVE_ADDRESS


async def remove_token_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await query.answer(
            "Only group administrators can do this.",
            show_alert=True,
        )

        return BUYBOT_REMOVE_ADDRESS

    try:

        token_id = int(
            query.data.replace(
                "buybot_remove_token_",
                "",
            )
        )

    except ValueError:

        await query.answer(
            "Invalid token.",
            show_alert=True,
        )

        return ConversationHandler.END

    pool = await get_pool()

    async with pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT
                token_name,
                token_symbol,
                chain,
                contract_address
            FROM buybot_tokens
            WHERE id = $1
              AND group_id = $2
            """,
            token_id,
            chat.id,
        )

        if not row:

            await query.edit_message_text(
                "❌ Token not found."
            )

            return ConversationHandler.END

        await conn.execute(
            """
            UPDATE buybot_tokens
            SET
                enabled = FALSE,
                updated_at = NOW()
            WHERE id = $1
              AND group_id = $2
            """,
            token_id,
            chat.id,
        )

    name = (
        row["token_symbol"]
        or row["token_name"]
        or "Token"
    )

    await query.edit_message_text(
        "✅ *TOKEN REMOVED*\n\n"
        f"Token: *{name}*\n"
        f"Network: *{row['chain'].upper()}*\n\n"
        "BuyBot will no longer monitor this token.",
        parse_mode="Markdown",
    )

    return ConversationHandler.END


async def buybot_token_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if query:
        await query.answer()

        await query.edit_message_text(
            "✅ Token management cancelled."
        )

    context.user_data.pop(
        "buybot_add_chain",
        None,
    )

    return ConversationHandler.END


# ============================================================
# INLINE BUTTON SETTINGS
# ============================================================

async def buybot_buttons_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await query.edit_message_text(
            "⛔ Only group administrators "
            "can use this."
        )

        return BUYBOT_MENU

    buttons = await get_buttons(
        chat.id
    )

    if buttons:

        button_lines = []

        for index, button in enumerate(
            buttons,
            start=1,
        ):

            button_lines.append(
                f"{index}. "
                f"🔗 {button['button_name']}"
            )

        current_buttons = "\n".join(
            button_lines
        )

    else:

        current_buttons = (
            "No custom buttons added yet."
        )

    text = (
        "🔗 *INLINE BUTTONS*\n\n"
        "These buttons appear underneath "
        "every BuyBot alert in this group.\n\n"
        f"*Current buttons: {len(buttons)}/"
        f"{MAX_CUSTOM_BUTTONS}*\n\n"
        f"{current_buttons}\n\n"
        "You can add up to 3 custom buttons."
    )

    keyboard = []

    if len(buttons) < MAX_CUSTOM_BUTTONS:

        keyboard.append(
            [
                InlineKeyboardButton(
                    "➕ Add Button",
                    callback_data=(
                        "buybot_add_button"
                    ),
                ),
            ]
        )

    if buttons:

        keyboard.append(
            [
                InlineKeyboardButton(
                    "🗑 Clear All",
                    callback_data=(
                        "buybot_clear_buttons"
                    ),
                ),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "↩️ Back",
                callback_data="buybot_back",
            ),
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="Markdown",
    )

    return BUYBOT_BUTTONS


async def buybot_add_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await query.edit_message_text(
            "⛔ Only group administrators "
            "can add buttons."
        )

        return BUYBOT_BUTTONS

    buttons = await get_buttons(
        chat.id
    )

    if len(buttons) >= MAX_CUSTOM_BUTTONS:

        await query.answer(
            "Maximum 3 custom buttons allowed.",
            show_alert=True,
        )

        return BUYBOT_BUTTONS

    await query.edit_message_text(
        "➕ *ADD INLINE BUTTON*\n\n"
        "Send the button in this format:\n\n"
        "`Button Name | Button Link`\n\n"
        "Example:\n"
        "`Website | https://example.com`\n\n"
        "The link must start with "
        "`https://` or `http://`.\n\n"
        "Maximum 3 buttons.",
        parse_mode="Markdown",
    )

    return BUYBOT_ADD_BUTTON


def valid_url(
    value: str,
) -> bool:

    try:

        parsed = urlparse(
            value
        )

        return (
            parsed.scheme in {
                "http",
                "https",
            }
            and bool(
                parsed.netloc
            )
        )

    except Exception:

        return False


async def buybot_receive_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await update.message.reply_text(
            "⛔ Only group administrators "
            "can add BuyBot buttons."
        )

        return BUYBOT_BUTTONS

    buttons = await get_buttons(
        chat.id
    )

    if len(buttons) >= MAX_CUSTOM_BUTTONS:

        await update.message.reply_text(
            "⚠️ You already have 3 custom buttons.\n\n"
            "Use Clear All first if you want "
            "to replace them."
        )

        return BUYBOT_BUTTONS

    raw = (
        update.message.text or ""
    ).strip()

    if "|" not in raw:

        await update.message.reply_text(
            "❌ Invalid format.\n\n"
            "Use:\n"
            "`Button Name | Button Link`\n\n"
            "Example:\n"
            "`Chart | https://dexscreener.com/`",
            parse_mode="Markdown",
        )

        return BUYBOT_ADD_BUTTON

    name, url = raw.split(
        "|",
        1,
    )

    name = name.strip()
    url = url.strip()

    if not name:

        await update.message.reply_text(
            "❌ Button name cannot be empty."
        )

        return BUYBOT_ADD_BUTTON

    if len(name) > 40:

        await update.message.reply_text(
            "❌ Button name is too long.\n"
            "Maximum: 40 characters."
        )

        return BUYBOT_ADD_BUTTON

    if not valid_url(url):

        await update.message.reply_text(
            "❌ Invalid link.\n\n"
            "The link must start with "
            "`https://` or `http://`.",
            parse_mode="Markdown",
        )

        return BUYBOT_ADD_BUTTON

    if len(url) > 2048:

        await update.message.reply_text(
            "❌ The link is too long."
        )

        return BUYBOT_ADD_BUTTON

    position = (
        len(buttons) + 1
    )

    pool = await get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO buybot_buttons (
                group_id,
                position,
                button_name,
                button_url
            )
            VALUES (
                $1,
                $2,
                $3,
                $4
            )
            """,
            chat.id,
            position,
            name,
            url,
        )

    await update.message.reply_text(
        "✅ *BUTTON ADDED*\n\n"
        f"🔗 Name: *{name}*\n"
        f"🌐 Link: `{url}`\n\n"
        f"Buttons used: "
        f"{position}/{MAX_CUSTOM_BUTTONS}",
        parse_mode="Markdown",
    )

    await send_buybot_menu(
        update,
        context,
    )

    return BUYBOT_MENU


async def buybot_clear_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    if not await is_group_admin(
        update,
        context,
    ):

        await query.edit_message_text(
            "⛔ Only group administrators "
            "can do this."
        )

        return BUYBOT_BUTTONS

    pool = await get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM buybot_buttons
            WHERE group_id = $1
            """,
            chat.id,
        )

    return await buybot_buttons_menu(
        update,
        context,
    )


# ============================================================
# PREVIEW / APPEARANCE
# ============================================================

async def buybot_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    chat = update.effective_chat

    if not chat:
        return ConversationHandler.END

    buttons = await get_buttons(
        chat.id
    )

    keyboard = []

    row = []

    for button in buttons:

        row.append(
            InlineKeyboardButton(
                button["button_name"],
                url=button["button_url"],
            )
        )

        if len(row) == 2:

            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append(
        [
            InlineKeyboardButton(
                "⚙️ BuyBot Settings",
                callback_data="buybot_back",
            ),
        ]
    )

    text = (
        "🚀 *BUY ALERT PREVIEW*\n\n"
        "💚 *LARREY* `$LARREY`\n\n"
        "🟢 *BUY DETECTED*\n\n"
        "💵 Spent: *$25.13*\n"
        "🔀 Native: *0.037 BNB*\n"
        "🪙 Received: *14,278,367,881,198 LARREY*\n\n"
        "👤 *New Holder*\n"
        "💎 Market Cap: *$786*\n\n"
        "⛓️ Network: *BNB Smart Chain*\n\n"
        "This is only a preview.\n"
        "Real values will come from the "
        "on-chain BuyBot engine."
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="Markdown",
    )

    return BUYBOT_MENU


async def buybot_appearance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🎨 *BUYBOT APPEARANCE*\n\n"
        "This section will control:\n\n"
        "🖼 Custom image / GIF / video\n"
        "🟢 Buy emoji\n"
        "💵 Spent emoji\n"
        "🪙 Received emoji\n"
        "👤 Holder indicator\n"
        "💎 Market-cap indicator\n"
        "💰 Minimum buy amount\n\n"
        "These settings will be connected "
        "to the live renderer in the next "
        "BuyBot settings stage.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "↩️ Back",
                        callback_data="buybot_back",
                    ),
                ],
            ]
        ),
        parse_mode="Markdown",
    )

    return BUYBOT_MENU


async def buybot_back(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    await send_buybot_menu(
        update,
        context,
    )

    return BUYBOT_MENU


async def buybot_close(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "✅ BuyBot settings closed.\n\n"
        "Use /buybot whenever you want "
        "to configure it again."
    )

    return ConversationHandler.END


async def buybot_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.message:

        await update.message.reply_text(
            "✅ BuyBot settings closed."
        )

    context.user_data.pop(
        "buybot_add_chain",
        None,
    )

    return ConversationHandler.END


# ============================================================
# CONVERSATION HANDLER
# ============================================================

def build_buybot_conversation():

    return ConversationHandler(

        entry_points=[

            CommandHandler(
                "buybot",
                buybot_start,
            ),

            CommandHandler(
                "buybotsettings",
                buybot_start,
            ),

            CommandHandler(
                "add",
                add_token_start,
            ),

            CommandHandler(
                "remove",
                remove_token_start,
            ),
        ],

        states={

            BUYBOT_MENU: [

                CallbackQueryHandler(
                    buybot_buttons_menu,
                    pattern=(
                        r"^buybot_buttons$"
                    ),
                ),

                CallbackQueryHandler(
                    buybot_preview,
                    pattern=(
                        r"^buybot_preview$"
                    ),
                ),

                CallbackQueryHandler(
                    buybot_appearance,
                    pattern=(
                        r"^buybot_appearance$"
                    ),
                ),

                CallbackQueryHandler(
                    buybot_back,
                    pattern=(
                        r"^buybot_back$"
                    ),
                ),

                CallbackQueryHandler(
                    buybot_close,
                    pattern=(
                        r"^buybot_close$"
                    ),
                ),
            ],

            BUYBOT_BUTTONS: [

                CallbackQueryHandler(
                    buybot_add_button,
                    pattern=(
                        r"^buybot_add_button$"
                    ),
                ),

                CallbackQueryHandler(
                    buybot_clear_buttons,
                    pattern=(
                        r"^buybot_clear_buttons$"
                    ),
                ),

                CallbackQueryHandler(
                    buybot_back,
                    pattern=(
                        r"^buybot_back$"
                    ),
                ),
            ],

            BUYBOT_ADD_BUTTON: [

                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    buybot_receive_button,
                ),
            ],

            BUYBOT_ADD_CHAIN: [

                CallbackQueryHandler(
                    add_token_chain_selected,
                    pattern=(
                        r"^buybot_add_chain_"
                        r"(bnb|ethereum|solana|robinhood)$"
                    ),
                ),

                CallbackQueryHandler(
                    buybot_token_cancel,
                    pattern=(
                        r"^buybot_token_cancel$"
                    ),
                ),
            ],

            BUYBOT_ADD_ADDRESS: [

                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    add_token_address_received,
                ),
            ],

            BUYBOT_REMOVE_ADDRESS: [

                CallbackQueryHandler(
                    remove_token_selected,
                    pattern=(
                        r"^buybot_remove_token_\d+$"
                    ),
                ),

                CallbackQueryHandler(
                    buybot_token_cancel,
                    pattern=(
                        r"^buybot_token_cancel$"
                    ),
                ),
            ],
        },

        fallbacks=[

            CommandHandler(
                "cancel",
                buybot_cancel,
            ),
        ],

        allow_reentry=True,

        per_user=True,

        per_chat=True,
    )
