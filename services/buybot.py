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


SELECT_TOKEN_CHAIN = 1
ENTER_TOKEN_ADDRESS = 2
ENTER_TOKEN_NAME = 3
ENTER_TOKEN_SYMBOL = 4

SELECT_REMOVE_TOKEN = 5

ADD_BUTTON_NAME = 6
ADD_BUTTON_URL = 7


SUPPORTED_CHAINS = {
    "bnb": "BNB Smart Chain",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "robinhood": "Robinhood",
}


def is_group(update: Update) -> bool:
    if not update.effective_chat:
        return False

    return update.effective_chat.type in {
        "group",
        "supergroup",
    }


async def is_group_admin(
    update: Update,
) -> bool:
    if not is_group(update):
        return False

    user = update.effective_user

    if not user:
        return False

    try:
        member = await update.effective_chat.get_member(
            user.id
        )

        return member.status in {
            "administrator",
            "creator",
        }

    except Exception as exc:
        print(
            "BuyBot admin check error: "
            f"{exc}"
        )
        return False


async def get_settings(group_id):
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
            WHERE group_id = $1
            LIMIT 1;
            """,
            group_id,
        )


async def ensure_settings(group_id):
    pool = await get_pool()

    async with pool.acquire() as connection:
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


async def set_buybot_enabled(
    group_id,
    enabled,
):
    await ensure_settings(group_id)

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE buybot_settings
            SET
                enabled = $2,
                updated_at = NOW()
            WHERE group_id = $1;
            """,
            group_id,
            enabled,
        )


async def get_buttons(group_id):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetch(
            """
            SELECT
                id,
                group_id,
                button_name,
                button_url,
                position
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC;
            """,
            group_id,
        )


async def get_tokens(group_id):
    pool = await get_pool()

    async with pool.acquire() as connection:
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
                enabled
            FROM buybot_tokens
            WHERE group_id = $1
            ORDER BY enabled DESC, created_at DESC;
            """,
            group_id,
        )


def main_menu():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🟢 Enable BuyBot",
                    callback_data="bb_enable",
                ),
                InlineKeyboardButton(
                    "🔴 Disable BuyBot",
                    callback_data="bb_disable",
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
                    "🎨 Customize Alert",
                    callback_data="bb_customize",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔘 Manage Buttons",
                    callback_data="bb_buttons",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📋 Monitored Tokens",
                    callback_data="bb_tokens",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data="bb_refresh",
                ),
        ]
    ])


async def buybot_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await is_group_admin(update):
        if update.message:
            await update.message.reply_text(
                "🔒 Only group administrators can "
                "manage BuyBot settings."
            )
        return ConversationHandler.END

    group_id = update.effective_chat.id

    await ensure_settings(group_id)

    settings = await get_settings(
        group_id
    )

    enabled = bool(
        settings["enabled"]
    ) if settings else False

    status = (
        "🟢 ENABLED"
        if enabled
        else "🔴 DISABLED"
    )

    await update.message.reply_text(
        "🤖 *BuyBot Settings*\n\n"
        f"Status: {status}\n\n"
        "Manage BuyBot directly from this menu.",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )

    return ConversationHandler.END


async def activate_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await is_group_admin(update):
        await update.message.reply_text(
            "🔒 Only group administrators can "
            "activate BuyBot."
        )
        return

    group_id = update.effective_chat.id

    await ensure_settings(group_id)

    await set_buybot_enabled(
        group_id,
        True,
    )

    await update.message.reply_text(
        "🟢 BuyBot activated for this group.\n\n"
        "Use /buybot to manage its settings."
    )


async def remove_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await is_group_admin(update):
        await update.message.reply_text(
            "🔒 Only group administrators can "
            "disable BuyBot."
        )
        return

    group_id = update.effective_chat.id

    await ensure_settings(group_id)

    await set_buybot_enabled(
        group_id,
        False,
    )

    await update.message.reply_text(
        "🔴 BuyBot disabled for this group."
    )


async def buybot_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return ConversationHandler.END

    await query.answer()

    if not await is_group_admin(update):
        await query.answer(
            "Only group administrators can do this.",
            show_alert=True,
        )
        return ConversationHandler.END

    group_id = update.effective_chat.id
    data = query.data

    await ensure_settings(group_id)

    if data == "bb_enable":
        await set_buybot_enabled(
            group_id,
            True,
        )

        await query.edit_message_text(
            "🟢 BuyBot is now enabled for this group.",
            reply_markup=main_menu(),
        )

        return ConversationHandler.END

    if data == "bb_disable":
        await set_buybot_enabled(
            group_id,
            False,
        )

        await query.edit_message_text(
            "🔴 BuyBot is now disabled for this group.",
            reply_markup=main_menu(),
        )

        return ConversationHandler.END

    if data == "bb_refresh":
        settings = await get_settings(
            group_id
        )

        enabled = bool(
            settings["enabled"]
        ) if settings else False

        status = (
            "🟢 ENABLED"
            if enabled
            else "🔴 DISABLED"
        )

        await query.edit_message_text(
            "🤖 BuyBot Settings\n\n"
            f"Status: {status}",
            reply_markup=main_menu(),
        )

        return ConversationHandler.END

    if data == "bb_tokens":
        tokens = await get_tokens(
            group_id
        )

        if not tokens:
            text = (
                "📋 *Monitored Tokens*\n\n"
                "No tokens have been added yet."
            )

        else:
            lines = [
                "📋 *Monitored Tokens*",
                "",
            ]

            for token in tokens:
                status = (
                    "🟢"
                    if token["enabled"]
                    else "⚪"
                )

                chain = SUPPORTED_CHAINS.get(
                    token["chain"],
                    token["chain"],
                )

                symbol = (
                    token["token_symbol"]
                    or "TOKEN"
                )

                address = token[
                    "contract_address"
                ]

                short_address = (
                    f"{address[:8]}..."
                    f"{address[-6:]}"
                )

                lines.append(
                    f"{status} {symbol} — {chain}"
                )
                lines.append(
                    f"`{short_address}`"
                )
                lines.append("")

            text = "\n".join(lines)

        await query.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

        return ConversationHandler.END

    if data == "bb_add_token":
        await query.edit_message_text(
            "➕ *Add BuyBot Token*\n\n"
            "Select the token's network:",
            reply_markup=chain_keyboard(
                "bb_add_chain_"
            ),
            parse_mode="Markdown",
        )

        return SELECT_TOKEN_CHAIN

    if data == "bb_remove_token":
        tokens = await get_tokens(
            group_id
        )

        enabled_tokens = [
            token
            for token in tokens
            if token["enabled"]
        ]

        if not enabled_tokens:
            await query.edit_message_text(
                "➖ No active monitored tokens "
                "were found.",
                reply_markup=main_menu(),
            )

            return ConversationHandler.END

        buttons = []

        for token in enabled_tokens:
            symbol = (
                token["token_symbol"]
                or "TOKEN"
            )

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"➖ {symbol}",
                        callback_data=(
                            f"bb_remove_{token['id']}"
                        ),
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="bb_back",
                )
            ]
        )

        await query.edit_message_text(
            "➖ Select a token to remove:",
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
        )

        return SELECT_REMOVE_TOKEN

    if data == "bb_customize":
        await query.edit_message_text(
            "🎨 *BuyBot Customization*\n\n"
            "Use these commands in the group:\n\n"
            "/buybottitle — change alert title\n"
            "/buybottemplate — change alert format\n"
            "/buybotemoji — customize emojis\n"
            "/buybotmedia — set GIF/photo/video\n"
            "/buybotmin — minimum buy amount\n\n"
            "The alert renderer supports custom "
            "templates and group-specific settings.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

        return ConversationHandler.END

    if data == "bb_buttons":
        buttons = await get_buttons(
            group_id
        )

        lines = [
            "🔘 *BuyBot Buttons*",
            "",
            "You can have up to 3 custom buttons.",
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
        else:
            lines.append(
                "No custom buttons configured."
            )

        keyboard = [
            [
                InlineKeyboardButton(
                    "➕ Add Button",
                    callback_data="bb_add_button",
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑 Clear Buttons",
                    callback_data="bb_clear_buttons",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="bb_back",
                )
            ],
        ]

        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="Markdown",
        )

        return ConversationHandler.END

    if data == "bb_add_button":
        buttons = await get_buttons(
            group_id
        )

        if len(buttons) >= 3:
            await query.answer(
                "Maximum 3 custom buttons allowed.",
                show_alert=True,
            )
            return ConversationHandler.END

        context.user_data[
            "buybot_button_group_id"
        ] = group_id

        await query.edit_message_text(
            "🔘 *Add Custom Button*\n\n"
            "Send the button name.\n\n"
            "Example:\n"
            "`Buy Token`",
            parse_mode="Markdown",
        )

        return ADD_BUTTON_NAME

    if data == "bb_clear_buttons":
        pool = await get_pool()

        async with pool.acquire() as connection:
            await connection.execute(
                """
                DELETE FROM buybot_buttons
                WHERE group_id = $1;
                """,
                group_id,
            )

        await query.edit_message_text(
            "🗑 All custom BuyBot buttons "
            "have been removed.",
            reply_markup=main_menu(),
        )

        return ConversationHandler.END

    if data == "bb_back":
        await query.edit_message_text(
            "🤖 *BuyBot Settings*",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

        return ConversationHandler.END

    if data.startswith("bb_remove_"):
        try:
            token_id = int(
                data.replace(
                    "bb_remove_",
                    "",
                    1,
                )
            )
        except ValueError:
            await query.edit_message_text(
                "❌ Invalid token selection.",
                reply_markup=main_menu(),
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

        if result == "UPDATE 0":
            text = (
                "❌ Token was not found."
            )
        else:
            text = (
                "✅ Token removed from "
                "active BuyBot monitoring."
            )

        await query.edit_message_text(
            text,
            reply_markup=main_menu(),
        )

        return ConversationHandler.END

    return ConversationHandler.END


def chain_keyboard(
    prefix,
):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🟡 BNB",
                    callback_data=(
                        f"{prefix}bnb"
                    ),
                ),
                InlineKeyboardButton(
                    "🔷 Ethereum",
                    callback_data=(
                        f"{prefix}ethereum"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "🟣 Solana",
                    callback_data=(
                        f"{prefix}solana"
                    ),
                ),
                InlineKeyboardButton(
                    "🔵 Robinhood",
                    callback_data=(
                        f"{prefix}robinhood"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="bb_cancel",
                )
            ],
        ]
    )


async def add_chain_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not await is_group_admin(update):
        await query.answer(
            "Only group administrators can add tokens.",
            show_alert=True,
        )
        return ConversationHandler.END

    data = query.data

    if data == "bb_cancel":
        await query.edit_message_text(
            "❌ Add token cancelled.",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END

    chain = data.replace(
        "bb_add_chain_",
        "",
        1,
    )

    if chain not in SUPPORTED_CHAINS:
        await query.edit_message_text(
            "❌ Unsupported chain.",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END

    context.user_data[
        "buybot_chain"
    ] = chain

    await query.edit_message_text(
        f"🔗 Network: "
        f"{SUPPORTED_CHAINS[chain]}\n\n"
        "Now send the token contract address.",
    )

    return ENTER_TOKEN_ADDRESS


def valid_evm_address(
    address,
):
    if not address:
        return False

    if not address.startswith("0x"):
        return False

    if len(address) != 42:
        return False

    try:
        int(address[2:], 16)
        return True
    except ValueError:
        return False


def valid_solana_address(
    address,
):
    if not address:
        return False

    if len(address) < 32:
        return False

    if len(address) > 44:
        return False

    alphabet = (
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        "abcdefghijkmnopqrstuvwxyz"
    )

    return all(
        character in alphabet
        for character in address
    )


async def token_address_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    address = (
        update.message.text.strip()
    )

    chain = context.user_data.get(
        "buybot_chain"
    )

    if chain in {
        "bnb",
        "ethereum",
        "robinhood",
    }:
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
            "❌ Invalid contract address for "
            "the selected network.\n\n"
            "Please send the correct token address."
        )
        return ENTER_TOKEN_ADDRESS

    context.user_data[
        "buybot_contract"
    ] = address

    await update.message.reply_text(
        "📝 Send the token name.\n\n"
        "Example: `Example Token`",
        parse_mode="Markdown",
    )

    return ENTER_TOKEN_NAME


async def token_name_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    name = (
        update.message.text.strip()
    )

    if not name:
        await update.message.reply_text(
            "❌ Token name cannot be empty."
        )
        return ENTER_TOKEN_NAME

    context.user_data[
        "buybot_token_name"
    ] = name

    await update.message.reply_text(
        "🔤 Send the token symbol.\n\n"
        "Example: `TOKEN`",
        parse_mode="Markdown",
    )

    return ENTER_TOKEN_SYMBOL


async def token_symbol_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    symbol = (
        update.message.text.strip()
        .upper()
    )

    if not symbol:
        await update.message.reply_text(
            "❌ Token symbol cannot be empty."
        )
        return ENTER_TOKEN_SYMBOL

    chain = context.user_data.get(
        "buybot_chain"
    )

    contract = context.user_data.get(
        "buybot_contract"
    )

    name = context.user_data.get(
        "buybot_token_name"
    )

    group_id = update.effective_chat.id

    pool = await get_pool()

    async with pool.acquire() as connection:
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
            ON CONFLICT (
                group_id,
                chain,
                contract_address
            )
            DO UPDATE SET
                token_name = EXCLUDED.token_name,
                token_symbol = EXCLUDED.token_symbol,
                enabled = TRUE,
                updated_at = NOW();
            """,
            group_id,
            chain,
            contract,
            name,
            symbol,
        )

    context.user_data.pop(
        "buybot_chain",
        None,
    )
    context.user_data.pop(
        "buybot_contract",
        None,
    )
    context.user_data.pop(
        "buybot_token_name",
        None,
    )

    await update.message.reply_text(
        "✅ Token added to BuyBot monitoring.\n\n"
        f"Token: {name} ({symbol})\n"
        f"Network: {SUPPORTED_CHAINS[chain]}\n"
        f"Contract: `{contract}`\n\n"
        "BuyBot will monitor the token when the "
        "group's BuyBot is enabled.",
        parse_mode="Markdown",
    )

    return ConversationHandler.END


async def remove_token_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await is_group_admin(update):
        await update.message.reply_text(
            "🔒 Only group administrators can "
            "remove BuyBot tokens."
        )
        return ConversationHandler.END

    tokens = await get_tokens(
        update.effective_chat.id
    )

    tokens = [
        token
        for token in tokens
        if token["enabled"]
    ]

    if not tokens:
        await update.message.reply_text(
            "📋 There are no active BuyBot tokens."
        )
        return ConversationHandler.END

    keyboard = []

    for token in tokens:
        symbol = (
            token["token_symbol"]
            or "TOKEN"
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"➖ {symbol}",
                    callback_data=(
                        f"bb_remove_{token['id']}"
                    ),
                )
            ]
        )

    await update.message.reply_text(
        "➖ Select the token to remove:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return ConversationHandler.END


async def list_tokens(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if is_group(update):
        group_id = update.effective_chat.id

        tokens = await get_tokens(
            group_id
        )

        if not tokens:
            await update.message.reply_text(
                "📋 No BuyBot tokens are configured "
                "for this group."
            )
            return

        lines = [
            "📋 BuyBot Tokens",
            "",
        ]

        for token in tokens:
            status = (
                "🟢 Active"
                if token["enabled"]
                else "⚪ Disabled"
            )

            chain = SUPPORTED_CHAINS.get(
                token["chain"],
                token["chain"],
            )

            symbol = (
                token["token_symbol"]
                or "TOKEN"
            )

            address = token[
                "contract_address"
            ]

            short_address = (
                f"{address[:8]}..."
                f"{address[-6:]}"
            )

            lines.append(
                f"{status} — {symbol}"
            )
            lines.append(
                f"Network: {chain}"
            )
            lines.append(
                f"Contract: {short_address}"
            )
            lines.append("")

        await update.message.reply_text(
            "\n".join(lines)
        )
        return

    await update.message.reply_text(
        "📋 /tokens is available inside "
        "a BuyBot-enabled group."
    )


async def button_name_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await is_group_admin(update):
        await update.message.reply_text(
            "🔒 Only group administrators can "
            "manage BuyBot buttons."
        )
        return ConversationHandler.END

    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "❌ Button name cannot be empty."
        )
        return ADD_BUTTON_NAME

    context.user_data[
        "buybot_button_name"
    ] = name

    await update.message.reply_text(
        "🔗 Now send the button link.\n\n"
        "Example:\n"
        "`https://example.com`",
        parse_mode="Markdown",
    )

    return ADD_BUTTON_URL


async def button_url_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await is_group_admin(update):
        await update.message.reply_text(
            "🔒 Only group administrators can "
            "manage BuyBot buttons."
        )
        return ConversationHandler.END

    url = update.message.text.strip()

    if not (
        url.startswith("https://")
        or url.startswith("http://")
    ):
        await update.message.reply_text(
            "❌ Please send a valid URL starting "
            "with http:// or https://."
        )
        return ADD_BUTTON_URL

    group_id = update.effective_chat.id

    name = context.user_data.get(
        "buybot_button_name"
    )

    if not name:
        await update.message.reply_text(
            "❌ Button session expired. "
            "Please use /buybot again."
        )
        return ConversationHandler.END

    buttons = await get_buttons(
        group_id
    )

    if len(buttons) >= 3:
        await update.message.reply_text(
            "❌ This group already has the "
            "maximum of 3 custom buttons."
        )
        return ConversationHandler.END

    position = len(buttons) + 1

    pool = await get_pool()

    async with pool.acquire() as connection:
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
            position,
        )

    context.user_data.pop(
        "buybot_button_name",
        None,
    )

    await update.message.reply_text(
        "✅ Custom BuyBot button added.\n\n"
        f"Button: {name}\n"
        f"Link: {url}"
    )

    return ConversationHandler.END


async def cancel_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop(
        "buybot_chain",
        None,
    )
    context.user_data.pop(
        "buybot_contract",
        None,
    )
    context.user_data.pop(
        "buybot_token_name",
        None,
    )
    context.user_data.pop(
        "buybot_button_name",
        None,
    )

    if update.callback_query:
        await update.callback_query.answer()

        await update.callback_query.edit_message_text(
            "❌ Cancelled.",
            reply_markup=main_menu(),
        )

    elif update.message:
        await update.message.reply_text(
            "❌ Cancelled."
        )

    return ConversationHandler.END


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
                lambda update, context: (
                    buybot_command(
                        update,
                        context,
                    )
                ),
            ),
            CommandHandler(
                "remove",
                remove_token_command,
            ),
        ],
        states={
            SELECT_TOKEN_CHAIN: [
                CallbackQueryHandler(
                    add_chain_selected,
                    pattern=(
                        r"^bb_add_chain_"
                        r"|^bb_cancel$"
                    ),
                ),
            ],
            ENTER_TOKEN_ADDRESS: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    token_address_received,
                ),
            ],
            ENTER_TOKEN_NAME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    token_name_received,
                ),
            ],
            ENTER_TOKEN_SYMBOL: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    token_symbol_received,
                ),
            ],
            SELECT_REMOVE_TOKEN: [
                CallbackQueryHandler(
                    buybot_callback,
                    pattern=(
                        r"^bb_remove_"
                        r"|^bb_back$"
                    ),
                ),
            ],
            ADD_BUTTON_NAME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    button_name_received,
                ),
            ],
            ADD_BUTTON_URL: [
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
            CallbackQueryHandler(
                cancel_buybot,
                pattern=r"^bb_cancel$",
            ),
        ],
        allow_reentry=True,
    )


def register_buybot_callbacks(
    application,
):
    application.add_handler(
        CallbackQueryHandler(
            buybot_callback,
            pattern=(
                r"^bb_enable$"
                r"|^bb_disable$"
                r"|^bb_refresh$"
                r"|^bb_tokens$"
                r"|^bb_add_token$"
                r"|^bb_remove_token$"
                r"|^bb_customize$"
                r"|^bb_buttons$"
                r"|^bb_add_button$"
                r"|^bb_clear_buttons$"
                r"|^bb_back$"
                r"|^bb_remove_"
            ),
        )
    )
