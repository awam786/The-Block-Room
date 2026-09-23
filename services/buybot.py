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

SELECT_ADD_CHAIN = 1
ENTER_ADD_TOKEN = 2

SELECT_REMOVE_TOKEN = 3

ENTER_BUTTON_NAME = 4
ENTER_BUTTON_URL = 5


# ============================================================
# SUPPORTED CHAINS
# ============================================================

CHAIN_LABELS = {
    "bnb": "🟡 BNB",
    "ethereum": "🔵 Ethereum",
    "solana": "🟣 Solana",
    "robinhood": "🔴 Robinhood",
}


# ============================================================
# ADMIN HELPERS
# ============================================================

async def is_group_admin(
    update: Update,
) -> bool:
    if not update.effective_chat:
        return False

    if update.effective_chat.type not in {
        "group",
        "supergroup",
    }:
        return False

    if not update.effective_user:
        return False

    try:
        member = await update.effective_chat.get_member(
            update.effective_user.id
        )
    except Exception:
        return False

    return member.status in {
        "administrator",
        "creator",
    }


async def require_group_admin(
    update: Update,
) -> bool:
    if await is_group_admin(
        update
    ):
        return True

    if update.callback_query:
        await update.callback_query.answer(
            "Only group admins can manage BuyBot.",
            show_alert=True,
        )
    elif update.message:
        await update.message.reply_text(
            "❌ Only group admins can manage BuyBot."
        )

    return False


def current_group_id(
    update: Update,
) -> Optional[int]:
    if not update.effective_chat:
        return None

    if update.effective_chat.type not in {
        "group",
        "supergroup",
    }:
        return None

    return update.effective_chat.id


# ============================================================
# DATABASE HELPERS
# ============================================================

async def get_buybot_settings(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
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
                network_emoji,
                updated_at
            FROM buybot_settings
            WHERE group_id = $1
            LIMIT 1;
            """,
            group_id,
        )

        if row:
            return row

        await connection.execute(
            """
            INSERT INTO buybot_settings (
                group_id
            )
            VALUES ($1)
            ON CONFLICT (group_id)
            DO NOTHING;
            """,
            group_id,
        )

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
                network_emoji,
                updated_at
            FROM buybot_settings
            WHERE group_id = $1
            LIMIT 1;
            """,
            group_id,
        )


async def set_buybot_enabled(
    group_id: int,
    enabled: bool,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO buybot_settings (
                group_id,
                enabled
            )
            VALUES ($1, $2)
            ON CONFLICT (group_id)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                updated_at = NOW();
            """,
            group_id,
            enabled,
        )


async def update_buybot_setting(
    group_id: int,
    column: str,
    value,
):
    allowed = {
        "alert_title",
        "alert_template",
        "buy_emoji",
        "new_holder_emoji",
        "market_cap_emoji",
        "spent_emoji",
        "received_emoji",
        "network_emoji",
        "media_type",
        "media_id",
        "min_buy_usd",
    }

    if column not in allowed:
        raise ValueError(
            "Invalid BuyBot setting."
        )

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            f"""
            INSERT INTO buybot_settings (
                group_id,
                {column}
            )
            VALUES ($1, $2)
            ON CONFLICT (group_id)
            DO UPDATE SET
                {column} = EXCLUDED.{column},
                updated_at = NOW();
            """,
            group_id,
            value,
        )


async def get_custom_buttons(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetch(
            """
            SELECT
                id,
                group_id,
                button_name,
                button_url,
                position,
                created_at
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC;
            """,
            group_id,
        )


async def clear_custom_buttons(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            DELETE FROM buybot_buttons
            WHERE group_id = $1;
            """,
            group_id,
        )


async def add_custom_button(
    group_id: int,
    name: str,
    url: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        count = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_buttons
            WHERE group_id = $1;
            """,
            group_id,
        )

        if int(count or 0) >= 3:
            return False

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
            group_id,
            name,
            url,
            int(count or 0) + 1,
        )

    return True


async def get_tokens(
    group_id: int,
    enabled_only: bool = True,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        if enabled_only:
            return await connection.fetch(
                """
                SELECT
                    id,
                    group_id,
                    chain,
                    contract_address,
                    token_name,
                    token_symbol,
                    pair_address,
                    dex_url,
                    enabled,
                    created_at,
                    updated_at
                FROM buybot_tokens
                WHERE group_id = $1
                  AND enabled = TRUE
                ORDER BY created_at DESC;
                """,
                group_id,
            )

        return await connection.fetch(
            """
            SELECT
                id,
                group_id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                pair_address,
                dex_url,
                enabled,
                created_at,
                updated_at
            FROM buybot_tokens
            WHERE group_id = $1
            ORDER BY created_at DESC;
            """,
            group_id,
        )


# ============================================================
# MAIN BUYBOT MENU
# ============================================================

def buybot_menu(
    enabled: bool,
):
    status = (
        "🟢 ACTIVE"
        if enabled
        else "🔴 INACTIVE"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                f"{status}",
                callback_data="bb_toggle",
            ),
        ],
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
                "📋 Tokens",
                callback_data="bb_tokens",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎨 Customize",
                callback_data="bb_customize",
            ),
            InlineKeyboardButton(
                "🔘 Buttons",
                callback_data="bb_buttons",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data="bb_refresh",
            ),
    ],
        [
            InlineKeyboardButton(
                "❌ Close",
                callback_data="bb_close",
            ),
        ],
    ]

    return InlineKeyboardMarkup(
        keyboard
    )


async def buybot_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    group_id = current_group_id(
        update
    )

    if group_id is None:
        await update.message.reply_text(
            "❌ Use /buybot inside a group."
        )
        return

    settings = await get_buybot_settings(
        group_id
    )

    await update.message.reply_text(
        "🤖 *BUYBOT CONTROL CENTER*\n\n"
        f"Status: "
        f"{'🟢 ACTIVE' if settings['enabled'] else '🔴 INACTIVE'}\n\n"
        "Group admins can manage monitored "
        "tokens, alert style, media and "
        "custom buttons from this menu.",
        parse_mode="Markdown",
        reply_markup=buybot_menu(
            settings["enabled"]
        ),
    )


# ============================================================
# ENABLE / DISABLE
# ============================================================

async def toggle_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    if not await require_group_admin(
        update
    ):
        return

    group_id = current_group_id(
        update
    )

    if group_id is None:
        return

    settings = await get_buybot_settings(
        group_id
    )

    new_state = not bool(
        settings["enabled"]
    )

    await set_buybot_enabled(
        group_id,
        new_state,
    )

    await query.answer(
        "BuyBot enabled."
        if new_state
        else "BuyBot disabled."
    )

    settings = await get_buybot_settings(
        group_id
    )

    await query.edit_message_reply_markup(
        reply_markup=buybot_menu(
            settings["enabled"]
        )
    )


# ============================================================
# ADD TOKEN
# ============================================================

def add_chain_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🟡 BNB",
                    callback_data="bb_chain_bnb",
                ),
                InlineKeyboardButton(
                    "🔵 Ethereum",
                    callback_data="bb_chain_ethereum",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🟣 Solana",
                    callback_data="bb_chain_solana",
                ),
                InlineKeyboardButton(
                    "🔴 Robinhood",
                    callback_data="bb_chain_robinhood",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="bb_cancel",
                ),
            ],
        ]
    )


async def add_token_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    group_id = current_group_id(
        update
    )

    if group_id is None:
        await update.message.reply_text(
            "❌ Use /add inside a group."
        )
        return ConversationHandler.END

    context.user_data[
        "buybot_group_id"
    ] = group_id

    context.user_data.pop(
        "buybot_chain",
        None,
    )

    await update.message.reply_text(
        "➕ *ADD BUYBOT TOKEN*\n\n"
        "Choose the blockchain:",
        parse_mode="Markdown",
        reply_markup=add_chain_keyboard(),
    )

    return SELECT_ADD_CHAIN


async def add_chain_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return SELECT_ADD_CHAIN

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    await query.answer()

    chain = (
        query.data
        .replace(
            "bb_chain_",
            "",
        )
        .strip()
        .lower()
    )

    if chain not in CHAIN_LABELS:
        await query.edit_message_text(
            "❌ Unsupported chain."
        )
        return ConversationHandler.END

    context.user_data[
        "buybot_chain"
    ] = chain

    await query.edit_message_text(
        f"{CHAIN_LABELS[chain]} selected.\n\n"
        "Send the token contract address."
    )

    return ENTER_ADD_TOKEN


async def add_token_address_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return ENTER_ADD_TOKEN

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    group_id = context.user_data.get(
        "buybot_group_id"
    )

    chain = context.user_data.get(
        "buybot_chain"
    )

    if not group_id or not chain:
        await update.message.reply_text(
            "❌ BuyBot session expired. "
            "Please use /add again."
        )
        return ConversationHandler.END

    address = (
        update.message.text
        or ""
    ).strip()

    if chain in {
        "bnb",
        "ethereum",
        "robinhood",
    }:
        valid = (
            len(address) == 42
            and address.startswith(
                "0x"
            )
        )

        if valid:
            try:
                int(
                    address[2:],
                    16,
                )
            except ValueError:
                valid = False

    else:
        alphabet = (
            "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
            "abcdefghijkmnopqrstuvwxyz"
        )

        valid = (
            32
            <= len(address)
            <= 44
            and all(
                character in alphabet
                for character in address
            )
        )

    if not valid:
        await update.message.reply_text(
            "❌ Invalid contract address "
            "for the selected chain.\n\n"
            "Please send a valid address."
        )
        return ENTER_ADD_TOKEN

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO buybot_tokens (
                group_id,
                chain,
                contract_address,
                enabled
            )
            VALUES (
                $1,
                $2,
                $3,
                TRUE
            )
            ON CONFLICT (
                group_id,
                chain,
                contract_address
            )
            DO UPDATE SET
                enabled = TRUE,
                updated_at = NOW();
            """,
            group_id,
            chain,
            address,
        )

    await update.message.reply_text(
        "✅ Token added to BuyBot monitoring.\n\n"
        f"Chain: {CHAIN_LABELS[chain]}\n"
        f"Contract: {address}"
    )

    context.user_data.pop(
        "buybot_group_id",
        None,
    )

    context.user_data.pop(
        "buybot_chain",
        None,
    )

    return ConversationHandler.END


# ============================================================
# REMOVE TOKEN
# ============================================================

async def remove_token_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    group_id = current_group_id(
        update
    )

    if group_id is None:
        await update.message.reply_text(
            "❌ Use /remove inside a group."
        )
        return ConversationHandler.END

    tokens = await get_tokens(
        group_id,
        enabled_only=True,
    )

    if not tokens:
        await update.message.reply_text(
            "📭 No monitored BuyBot tokens."
        )
        return ConversationHandler.END

    keyboard = []

    for token in tokens:
        label = (
            f"{token['token_symbol'] or 'Token'} "
            f"• {token['chain'].upper()}"
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=(
                        f"bb_remove_token_{token['id']}"
                    ),
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="bb_cancel",
            )
        ]
    )

    await update.message.reply_text(
        "➖ *REMOVE BUYBOT TOKEN*\n\n"
        "Select a token to stop monitoring:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return SELECT_REMOVE_TOKEN


async def remove_token_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return ConversationHandler.END

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    await query.answer()

    group_id = current_group_id(
        update
    )

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

    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE buybot_tokens
            SET
                enabled = FALSE,
                updated_at = NOW()
            WHERE id = $1
              AND group_id = $2;
            """,
            token_id,
            group_id,
        )

    if result.endswith("0"):
        await query.edit_message_text(
            "❌ Token was not found."
        )
        return ConversationHandler.END

    await query.edit_message_text(
        "✅ Token removed from active BuyBot monitoring."
    )

    return ConversationHandler.END


# ============================================================
# TOKEN LIST
# ============================================================

async def list_tokens(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    group_id = current_group_id(
        update
    )

    if group_id is None:
        await update.message.reply_text(
            "❌ Use /tokens inside a group."
        )
        return

    tokens = await get_tokens(
        group_id,
        enabled_only=False,
    )

    if not tokens:
        await update.message.reply_text(
            "📭 No BuyBot tokens have been added."
        )
        return

    lines = [
        "📋 BUYBOT TOKENS",
        "",
    ]

    for token in tokens:
        status = (
            "🟢 Active"
            if token["enabled"]
            else "⚪ Disabled"
        )

        symbol = (
            token["token_symbol"]
            or "Unknown"
        )

        lines.append(
            f"{status} • "
            f"{symbol} • "
            f"{token['chain'].upper()}"
        )

        lines.append(
            f"`{token['contract_address']}`"
        )

        lines.append("")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# ============================================================
# CUSTOMIZATION MENU
# ============================================================

def customization_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📝 Alert Title",
                    callback_data="bb_custom_title",
                ),
                InlineKeyboardButton(
                    "📄 Alert Template",
                    callback_data="bb_custom_template",
                ),
            ],
            [
                InlineKeyboardButton(
                    "😀 Emojis",
                    callback_data="bb_custom_emojis",
                ),
                InlineKeyboardButton(
                    "🖼 Media",
                    callback_data="bb_custom_media",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💵 Minimum Buy",
                    callback_data="bb_custom_min",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="bb_back",
                ),
            ],
        ]
    )


async def show_customization(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    if not await require_group_admin(
        update
    ):
        return

    await query.answer()

    await query.edit_message_text(
        "🎨 *BUYBOT CUSTOMIZATION*\n\n"
        "Choose what you want to customize.\n\n"
        "You can create a completely different "
        "alert style for your group.",
        parse_mode="Markdown",
        reply_markup=customization_keyboard(),
    )


# ============================================================
# CUSTOM TITLE
# ============================================================

async def title_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    if current_group_id(
        update
    ) is None:
        await update.message.reply_text(
            "❌ Use this command inside a group."
        )
        return

    await update.message.reply_text(
        "📝 Send the new BuyBot alert title."
    )

    context.user_data[
        "buybot_edit"
    ] = "alert_title"


# ============================================================
# CUSTOM TEMPLATE
# ============================================================

async def template_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    if current_group_id(
        update
    ) is None:
        await update.message.reply_text(
            "❌ Use this command inside a group."
        )
        return

    await update.message.reply_text(
        "📄 Send the new BuyBot alert template.\n\n"
        "Available placeholders:\n"
        "{title}\n"
        "{token_name}\n"
        "{token_symbol}\n"
        "{spent}\n"
        "{received}\n"
        "{buyer_short}\n"
        "{market_cap}\n"
        "{network}\n"
        "{tx_short}\n"
        "{buy_emoji}\n"
        "{new_holder_emoji}\n"
        "{market_cap_emoji}\n"
        "{spent_emoji}\n"
        "{received_emoji}\n"
        "{network_emoji}"
    )

    context.user_data[
        "buybot_edit"
    ] = "alert_template"


# ============================================================
# MINIMUM BUY
# ============================================================

async def min_buy_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    if current_group_id(
        update
    ) is None:
        await update.message.reply_text(
            "❌ Use this command inside a group."
        )
        return

    await update.message.reply_text(
        "💵 Send the minimum buy amount in USD.\n\n"
        "Example: 25"
    )

    context.user_data[
        "buybot_edit"
    ] = "min_buy_usd"


# ============================================================
# EMOJI CUSTOMIZATION
# ============================================================

async def emoji_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    if current_group_id(
        update
    ) is None:
        await update.message.reply_text(
            "😀 Send the six emojis in this order:\n\n"
            "1. Buy\n"
            "2. New Holder\n"
            "3. Market Cap\n"
            "4. Spent\n"
            "5. Received\n"
            "6. Network\n\n"
            "Example:\n"
            "🟢 👤 💎 💰 📦 ⛓️"
        )

        context.user_data[
            "buybot_edit"
        ] = "emojis"
        return

    await update.message.reply_text(
        "😀 Send the six emojis in this order:\n\n"
        "1. Buy\n"
        "2. New Holder\n"
        "3. Market Cap\n"
        "4. Spent\n"
        "5. Received\n"
        "6. Network\n\n"
        "Example:\n"
        "🟢 👤 💎 💰 📦 ⛓️"
    )

    context.user_data[
        "buybot_edit"
    ] = "emojis"


# ============================================================
# MEDIA CUSTOMIZATION
# ============================================================

async def media_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    if current_group_id(
        update
    ) is None:
        await update.message.reply_text(
            "❌ Use this command inside a group."
        )
        return

    await update.message.reply_text(
        "🖼 Send the BuyBot media now.\n\n"
        "Supported:\n"
        "• Photo\n"
        "• Video\n"
        "• GIF / animation\n\n"
        "Send /removemedia to remove it."
    )

    context.user_data[
        "buybot_edit"
    ] = "media"


async def remove_media_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    group_id = current_group_id(
        update
    )

    if group_id is None:
        return

    await update_buybot_setting(
        group_id,
        "media_type",
        None,
    )

    await update_buybot_setting(
        group_id,
        "media_id",
        None,
    )

    await update.message.reply_text(
        "✅ BuyBot media removed."
    )


# ============================================================
# TEXT EDIT HANDLER
# ============================================================

async def buybot_edit_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return False

    edit_type = context.user_data.get(
        "buybot_edit"
    )

    if not edit_type:
        return False

    if not await require_group_admin(
        update
    ):
        return True

    group_id = current_group_id(
        update
    )

    if group_id is None:
        return True

    text = (
        update.message.text
        or ""
    ).strip()

    if edit_type == "alert_title":
        if not text:
            await update.message.reply_text(
                "❌ Title cannot be empty."
            )
            return True

        await update_buybot_setting(
            group_id,
            "alert_title",
            text[:200],
        )

        await update.message.reply_text(
            "✅ BuyBot title updated."
        )

    elif edit_type == "alert_template":
        if not text:
            await update.message.reply_text(
                "❌ Template cannot be empty."
            )
            return True

        await update_buybot_setting(
            group_id,
            "alert_template",
            text[:4000],
        )

        await update.message.reply_text(
            "✅ BuyBot template updated."
        )

    elif edit_type == "min_buy_usd":
        try:
            value = float(text)

            if value < 0:
                raise ValueError

        except ValueError:
            await update.message.reply_text(
                "❌ Send a valid positive USD amount."
            )
            return True

        await update_buybot_setting(
            group_id,
            "min_buy_usd",
            value,
        )

        await update.message.reply_text(
            "✅ Minimum buy amount updated."
        )

    elif edit_type == "emojis":
        parts = text.split()

        if len(parts) != 6:
            await update.message.reply_text(
                "❌ Please send exactly 6 emojis."
            )
            return True

        columns = [
            "buy_emoji",
            "new_holder_emoji",
            "market_cap_emoji",
            "spent_emoji",
            "received_emoji",
            "network_emoji",
        ]

        for column, emoji in zip(
            columns,
            parts,
        ):
            await update_buybot_setting(
                group_id,
                column,
                emoji,
            )

        await update.message.reply_text(
            "✅ BuyBot emojis updated."
        )

    context.user_data.pop(
        "buybot_edit",
        None,
    )

    return True


# ============================================================
# MEDIA MESSAGE HANDLER
# ============================================================

async def buybot_media_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    edit_type = context.user_data.get(
        "buybot_edit"
    )

    if edit_type != "media":
        return False

    if not await require_group_admin(
        update
    ):
        return True

    group_id = current_group_id(
        update
    )

    if group_id is None:
        return True

    media_type = None
    media_id = None

    if update.message.photo:
        media_type = "photo"
        media_id = (
            update.message.photo[-1].file_id
        )

    elif update.message.video:
        media_type = "video"
        media_id = (
            update.message.video.file_id
        )

    elif update.message.animation:
        media_type = "animation"
        media_id = (
            update.message.animation.file_id
        )

    if not media_id:
        await update.message.reply_text(
            "❌ Please send a photo, video or GIF."
        )
        return True

    await update_buybot_setting(
        group_id,
        "media_type",
        media_type,
    )

    await update_buybot_setting(
        group_id,
        "media_id",
        media_id,
    )

    context.user_data.pop(
        "buybot_edit",
        None,
    )

    await update.message.reply_text(
        "✅ BuyBot media updated."
    )

    return True


# ============================================================
# CUSTOM BUTTONS
# ============================================================

async def show_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    if not await require_group_admin(
        update
    ):
        return

    await query.answer()

    group_id = current_group_id(
        update
    )

    buttons = await get_custom_buttons(
        group_id
    )

    lines = [
        "🔘 *CUSTOM BUYBOT BUTTONS*",
        "",
        "Maximum: 3 buttons.",
        "",
    ]

    if buttons:
        for index, button in enumerate(
            buttons,
            start=1,
        ):
            lines.append(
                f"{index}. "
                f"{button['button_name']}"
            )
            lines.append(
                f"{button['button_url']}"
            )
            lines.append("")

    else:
        lines.append(
            "No custom buttons configured."
        )

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Add Button",
                callback_data="bb_add_button",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Clear All",
                callback_data="bb_clear_buttons",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="bb_back",
            ),
        ],
    ]

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


async def add_button_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return ConversationHandler.END

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    await query.answer()

    group_id = current_group_id(
        update
    )

    buttons = await get_custom_buttons(
        group_id
    )

    if len(buttons) >= 3:
        await query.answer(
            "Maximum 3 custom buttons.",
            show_alert=True,
        )
        return ConversationHandler.END

    context.user_data[
        "buybot_button_group_id"
    ] = group_id

    await query.edit_message_text(
        "🔘 Send the button name.\n\n"
        "Example:\n"
        "Chart"
    )

    return ENTER_BUTTON_NAME


async def button_name_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return ENTER_BUTTON_NAME

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
        return ENTER_BUTTON_NAME

    context.user_data[
        "buybot_button_name"
    ] = name[:64]

    await update.message.reply_text(
        "🔗 Now send the button URL.\n\n"
        "Example:\n"
        "https://dexscreener.com/"
    )

    return ENTER_BUTTON_URL


async def button_url_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return ENTER_BUTTON_URL

    if not await require_group_admin(
        update
    ):
        return ConversationHandler.END

    group_id = context.user_data.get(
        "buybot_button_group_id"
    )

    name = context.user_data.get(
        "buybot_button_name"
    )

    url = (
        update.message.text
        or ""
    ).strip()

    if not (
        url.startswith(
            "https://"
        )
        or url.startswith(
            "http://"
        )
    ):
        await update.message.reply_text(
            "❌ Please send a valid HTTP/HTTPS URL."
        )
        return ENTER_BUTTON_URL

    if not group_id or not name:
        await update.message.reply_text(
            "❌ Button session expired."
        )
        return ConversationHandler.END

    success = await add_custom_button(
        group_id,
        name,
        url,
    )

    if not success:
        await update.message.reply_text(
            "❌ Maximum of 3 custom buttons "
            "has already been reached."
        )
    else:
        await update.message.reply_text(
            "✅ Custom button added."
        )

    context.user_data.pop(
        "buybot_button_group_id",
        None,
    )

    context.user_data.pop(
        "buybot_button_name",
        None,
    )

    return ConversationHandler.END


async def clear_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    if not await require_group_admin(
        update
    ):
        return

    await query.answer()

    group_id = current_group_id(
        update
    )

    await clear_custom_buttons(
        group_id
    )

    await query.edit_message_text(
        "✅ All custom BuyBot buttons "
        "have been cleared."
    )


# ============================================================
# GENERAL BUYBOT CALLBACK
# ============================================================

async def buybot_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    if not await require_group_admin(
        update
    ):
        return

    data = query.data or ""

    if data == "bb_toggle":
        await toggle_buybot(
            update,
            context,
        )
        return

    if data == "bb_refresh":
        await query.answer(
            "BuyBot settings refreshed."
        )

        group_id = current_group_id(
            update
        )

        settings = await get_buybot_settings(
            group_id
        )

        await query.edit_message_reply_markup(
            reply_markup=buybot_menu(
                settings["enabled"]
            )
        )
        return

    if data == "bb_tokens":
        await query.answer()

        group_id = current_group_id(
            update
        )

        tokens = await get_tokens(
            group_id,
            enabled_only=False,
        )

        if not tokens:
            text = (
                "📭 No BuyBot tokens configured."
            )
        else:
            lines = [
                "📋 *BUYBOT TOKENS*",
                "",
            ]

            for token in tokens:
                status = (
                    "🟢"
                    if token["enabled"]
                    else "⚪"
                )

                lines.append(
                    f"{status} "
                    f"{token['token_symbol'] or 'Unknown'} "
                    f"• {token['chain'].upper()}"
                )

            text = "\n".join(lines)

        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Back",
                            callback_data="bb_back",
                        )
                    ]
                ]
            ),
        )
        return

    if data == "bb_customize":
        await show_customization(
            update,
            context,
        )
        return

    if data == "bb_buttons":
        await show_buttons(
            update,
            context,
        )
        return

    if data == "bb_clear_buttons":
        await clear_buttons(
            update,
            context,
        )
        return

    if data == "bb_add_button":
        return

    if data == "bb_add_token":
        await query.answer(
            "Use /add to add a token."
        )
        await query.message.reply_text(
            "➕ Use /add to add a monitored token."
        )
        return

    if data == "bb_remove_token":
        await query.answer(
            "Use /remove to remove a token."
        )
        await query.message.reply_text(
            "➖ Use /remove to remove a monitored token."
        )
        return

    if data == "bb_back":
        await query.answer()

        group_id = current_group_id(
            update
        )

        settings = await get_buybot_settings(
            group_id
        )

        await query.edit_message_text(
            "🤖 *BUYBOT CONTROL CENTER*",
            parse_mode="Markdown",
            reply_markup=buybot_menu(
                settings["enabled"]
            ),
        )
        return

    if data == "bb_close":
        await query.answer()
        await query.delete_message()
        return

    if data == "bb_custom_title":
        await query.answer()
        await query.message.reply_text(
            "📝 Use /buybottitle to change the alert title."
        )
        return

    if data == "bb_custom_template":
        await query.answer()
        await query.message.reply_text(
            "📄 Use /buybottemplate to change the alert template."
        )
        return

    if data == "bb_custom_emojis":
        await query.answer()
        await query.message.reply_text(
            "😀 Use /buybotemoji to customize the alert emojis."
        )
        return

    if data == "bb_custom_media":
        await query.answer()
        await query.message.reply_text(
            "🖼 Use /buybotmedia to customize the BuyBot media."
        )
        return

    if data == "bb_custom_min":
        await query.answer()
        await query.message.reply_text(
            "💵 Use /buybotmin to change the minimum buy."
        )
        return

    if data == "bb_cancel":
        await query.answer(
            "Cancelled."
        )

        try:
            await query.edit_message_text(
                "❌ Cancelled."
            )
        except Exception:
            pass

        return ConversationHandler.END

    await query.answer()


# ============================================================
# ADMIN DIRECT COMMANDS
# ============================================================

async def activate_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    group_id = current_group_id(
        update
    )

    if group_id is None:
        await update.message.reply_text(
            "❌ Use this command inside a group."
        )
        return

    await set_buybot_enabled(
        group_id,
        True,
    )

    await update.message.reply_text(
        "🟢 BuyBot activated for this group."
    )


async def remove_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_group_admin(
        update
    ):
        return

    group_id = current_group_id(
        update
    )

    if group_id is None:
        await update.message.reply_text(
            "❌ Use this command inside a group."
        )
        return

    await set_buybot_enabled(
        group_id,
        False,
    )

    await update.message.reply_text(
        "🔴 BuyBot disabled for this group."
    )


# ============================================================
# CONVERSATION BUILDERS
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
                add_token_start,
            ),
            CommandHandler(
                "remove",
                remove_token_start,
            ),
        ],
        states={
            SELECT_ADD_CHAIN: [
                CallbackQueryHandler(
                    add_chain_selected,
                    pattern=r"^bb_chain_",
                ),
                CallbackQueryHandler(
                    lambda update, context:
                    ConversationHandler.END,
                    pattern=r"^bb_cancel$",
                ),
            ],
            ENTER_ADD_TOKEN: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    add_token_address_received,
                ),
            ],
            SELECT_REMOVE_TOKEN: [
                CallbackQueryHandler(
                    remove_token_selected,
                    pattern=r"^bb_remove_token_",
                ),
                CallbackQueryHandler(
                    lambda update, context:
                    ConversationHandler.END,
                    pattern=r"^bb_cancel$",
                ),
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                lambda update, context:
                ConversationHandler.END,
            ),
        ],
        allow_reentry=True,
    )


def build_buybot_button_conversation():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                add_button_start,
                pattern=r"^bb_add_button$",
            ),
        ],
        states={
            ENTER_BUTTON_NAME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    button_name_received,
                ),
            ],
            ENTER_BUTTON_URL: [
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
                lambda update, context:
                ConversationHandler.END,
            ),
        ],
        allow_reentry=True,
    )


# ============================================================
# CALLBACK REGISTRATION
# ============================================================

def register_buybot_callbacks(
    application,
):
    application.add_handler(
        CallbackQueryHandler(
            buybot_callback,
            pattern=(
                r"^bb_toggle$"
                r"|^bb_refresh$"
                r"|^bb_tokens$"
                r"|^bb_customize$"
                r"|^bb_buttons$"
                r"|^bb_clear_buttons$"
                r"|^bb_add_token$"
                r"|^bb_remove_token$"
                r"|^bb_back$"
                r"|^bb_close$"
                r"|^bb_custom_title$"
                r"|^bb_custom_template$"
                r"|^bb_custom_emojis$"
                r"|^bb_custom_media$"
                r"|^bb_custom_min$"
            ),
        )
    )

    application.add_handler(
        build_buybot_button_conversation()
    )

    application.add_handler(
        CommandHandler(
            "buybottitle",
            title_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "buybottemplate",
            template_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "buybotemoji",
            emoji_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "buybotmedia",
            media_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "removemedia",
            remove_media_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "buybotmin",
            min_buy_command,
        )
    )

    application.add_handler(
        MessageHandler(
            (
                filters.PHOTO
                | filters.VIDEO
                | filters.ANIMATION
            ),
            buybot_media_received,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            buybot_edit_message,
        )
    )
