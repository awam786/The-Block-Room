from urllib.parse import urlparse

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
) = range(3)


def is_main_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


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
        member = await context.bot.get_chat_member(
            chat.id,
            user.id,
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

    pool = get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO groups (
                telegram_id,
                title,
                username,
                is_active
            )
            VALUES (
                $1,
                $2,
                $3,
                TRUE
            )
            ON CONFLICT (telegram_id)
            DO UPDATE SET
                title = EXCLUDED.title,
                username = EXCLUDED.username
            """,
            chat.id,
            chat.title,
            chat.username,
        )


async def get_buybot_status(
    group_id: int,
):
    pool = get_pool()

    async with pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT
                enabled,
                min_buy_usd,
                show_new_holder,
                show_market_cap,
                show_spent_amount,
                show_received_amount,
                custom_media_type,
                buy_emoji,
                spent_emoji,
                received_emoji,
                holder_emoji,
                market_cap_emoji
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
    pool = get_pool()

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
            ORDER BY position ASC
            """,
            group_id,
        )

    return rows


async def is_buybot_enabled(
    group_id: int,
) -> bool:

    pool = get_pool()

    async with pool.acquire() as conn:

        result = await conn.fetchval(
            """
            SELECT buybot_enabled
            FROM groups
            WHERE telegram_id = $1
            """,
            group_id,
        )

    return bool(result)


async def activate_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_main_admin(user.id):
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
            "/Activebuybot <group_id>\n\n"
            "Or run /Activebuybot directly "
            "inside the target group."
        )

        return

    try:
        target_chat = await context.bot.get_chat(
            target_group_id
        )
    except Exception:
        await update.message.reply_text(
            "❌ I could not access that group.\n\n"
            "Make sure the bot has been added to the "
            "group first."
        )
        return

    pool = get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO groups (
                telegram_id,
                title,
                username,
                is_active,
                buybot_enabled
            )
            VALUES (
                $1,
                $2,
                $3,
                TRUE,
                TRUE
            )
            ON CONFLICT (telegram_id)
            DO UPDATE SET
                title = EXCLUDED.title,
                username = EXCLUDED.username,
                is_active = TRUE,
                buybot_enabled = TRUE
            """,
            target_chat.id,
            target_chat.title,
            target_chat.username,
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
            ON CONFLICT (group_id)
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
        "Group administrators can now open:\n"
        "👉 /buybot\n\n"
        "and configure the BuyBot themselves.",
        parse_mode="Markdown",
    )


async def remove_buybot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_main_admin(user.id):
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

    pool = get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE groups
            SET
                buybot_enabled = FALSE,
                updated_at = NOW()
            WHERE telegram_id = $1
            """,
            target_group_id,
        )

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

    await update.message.reply_text(
        "🔴 *BUYBOT DISABLED*\n\n"
        f"Group ID: `{target_group_id}`",
        parse_mode="Markdown",
    )


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

    await ensure_group(update)

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

    buttons = await get_buttons(
        chat.id
    )

    status = await get_buybot_status(
        chat.id
    )

    button_count = len(buttons)

    if status:
        enabled_text = (
            "🟢 Active"
            if status["enabled"]
            else "🔴 Disabled"
        )
    else:
        enabled_text = "🟢 Active"

    text = (
        "🤖 *THE BLOCK ROOM BUYBOT*\n\n"
        f"Status: {enabled_text}\n\n"
        "Manage how BuyBot alerts appear in "
        "this group.\n\n"
        "🔗 *Custom inline buttons:* "
        f"{button_count}/{MAX_CUSTOM_BUTTONS}\n\n"
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

    else:

        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )


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
        await query.answer(
            "Only group administrators can use this.",
            show_alert=True,
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

        current_buttons = (
            "\n".join(button_lines)
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
                    callback_data="buybot_add_button",
                ),
            ]
        )

    if buttons:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🗑 Clear All",
                    callback_data="buybot_clear_buttons",
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
        await query.answer(
            "Only group administrators can add buttons.",
            show_alert=True,
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
        parsed = urlparse(value)

        return parsed.scheme in {
            "http",
            "https",
        } and bool(parsed.netloc)

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
            "Use 🗑 Clear All first if you want "
            "to replace them."
        )

        await send_buybot_menu(
            update,
            context,
        )

        return BUYBOT_MENU

    raw = (
        update.message.text
        or ""
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

    position = len(buttons) + 1

    pool = get_pool()

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
        await query.answer(
            "Only group administrators can do this.",
            show_alert=True,
        )

        return BUYBOT_BUTTONS

    pool = get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM buybot_buttons
            WHERE group_id = $1
            """,
            chat.id,
        )

    await query.answer(
        "All custom buttons cleared."
    )

    await buybot_buttons_menu(
        update,
        context,
    )

    return BUYBOT_BUTTONS


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
        "🚀 *BUYBOT PREVIEW*\n\n"
        "💚 *LARREY* `$LARREY`\n\n"
        "🟢 *BUY:* $25.13\n"
        "🔀 *Spent:* 0.037 BNB\n"
        "🪙 *Received:* 14,278,367,881,198 LARREY\n\n"
        "👤 *New Holder*\n"
        "💎 *Market Cap:* $786\n\n"
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
        "Custom GIF/video/image, emojis and "
        "alert formatting will be configured "
        "here.\n\n"
        "This section is being connected to "
        "the BuyBot renderer next.",
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
    await update.message.reply_text(
        "✅ BuyBot settings closed."
    )

    return ConversationHandler.END


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
        ],

        states={

            BUYBOT_MENU: [
                CallbackQueryHandler(
                    buybot_buttons_menu,
                    pattern=r"^buybot_buttons$",
                ),
                CallbackQueryHandler(
                    buybot_preview,
                    pattern=r"^buybot_preview$",
                ),
                CallbackQueryHandler(
                    buybot_appearance,
                    pattern=r"^buybot_appearance$",
                ),
                CallbackQueryHandler(
                    buybot_back,
                    pattern=r"^buybot_back$",
                ),
                CallbackQueryHandler(
                    buybot_close,
                    pattern=r"^buybot_close$",
                ),
            ],

            BUYBOT_BUTTONS: [
                CallbackQueryHandler(
                    buybot_add_button,
                    pattern=r"^buybot_add_button$",
                ),
                CallbackQueryHandler(
                    buybot_clear_buttons,
                    pattern=r"^buybot_clear_buttons$",
                ),
                CallbackQueryHandler(
                    buybot_back,
                    pattern=r"^buybot_back$",
                ),
            ],

            BUYBOT_ADD_BUTTON: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    buybot_receive_button,
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
