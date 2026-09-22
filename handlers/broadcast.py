import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    filters,
)

from config import ADMIN_IDS
from database.connection import get_pool


# =========================================================
# STATES
# =========================================================

BROADCAST_CONTENT = 1
DISCOUNT_CONTENT = 2


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# =========================================================
# HELPERS
# =========================================================

async def save_target(
    target_id: int,
    target_type: str,
    title: str = None,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO broadcast_targets (
                target_id,
                target_type,
                title,
                active
            )
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (target_id)
            DO UPDATE SET
                target_type = EXCLUDED.target_type,
                title = EXCLUDED.title,
                active = TRUE,
                updated_at = NOW()
            """,
            target_id,
            target_type,
            title,
        )


async def get_users():
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT telegram_id
            FROM users
            WHERE telegram_id IS NOT NULL
            """
        )

    return [row["telegram_id"] for row in rows]


async def get_groups():
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT telegram_id
            FROM groups
            WHERE is_active = TRUE
            """
        )

    return [row["telegram_id"] for row in rows]


async def get_active_broadcast_targets():
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                target_id,
                target_type
            FROM broadcast_targets
            WHERE active = TRUE
            """
        )

    return [
        (
            row["target_id"],
            row["target_type"],
        )
        for row in rows
    ]


# =========================================================
# REGISTER USER
# =========================================================

async def register_user(
    update: Update,
):
    user = update.effective_user

    if not user:
        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO users (
                telegram_id,
                username,
                first_name,
                last_name
            )
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (telegram_id)
            DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                updated_at = NOW()
            """,
            user.id,
            user.username,
            user.first_name,
            user.last_name,
        )


# =========================================================
# REGISTER GROUP
# =========================================================

async def register_group(
    update: Update,
):
    chat = update.effective_chat

    if not chat:
        return

    if chat.type not in (
        "group",
        "supergroup",
    ):
        return

    await save_target(
        target_id=chat.id,
        target_type="group",
        title=chat.title,
    )

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO groups (
                telegram_id,
                title,
                username,
                type,
                is_active
            )
            VALUES ($1, $2, $3, $4, TRUE)
            ON CONFLICT (telegram_id)
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


# =========================================================
# BROADCAST COMMAND
# =========================================================

async def broadcast_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    context.user_data.pop(
        "broadcast_message_id",
        None,
    )

    context.user_data.pop(
        "broadcast_chat_id",
        None,
    )

    await update.message.reply_text(
        "📢 *Broadcast System*\n\n"
        "Send the message you want to broadcast.\n\n"
        "You can send:\n"
        "• Text\n"
        "• Photo + caption\n"
        "• Video + caption\n"
        "• Animation/GIF\n"
        "• Document\n\n"
        "After receiving it, I will show you "
        "the broadcast options.",
        parse_mode="Markdown",
    )

    return BROADCAST_CONTENT


# =========================================================
# RECEIVE BROADCAST CONTENT
# =========================================================

async def broadcast_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return ConversationHandler.END

    message = update.message

    if not message:
        return BROADCAST_CONTENT

    context.user_data[
        "broadcast_message_id"
    ] = message.message_id

    context.user_data[
        "broadcast_chat_id"
    ] = message.chat_id

    keyboard = [
        [
            InlineKeyboardButton(
                "👤 Broadcast Users",
                callback_data="broadcast_users",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Broadcast Groups",
                callback_data="broadcast_groups",
            ),
        ],
        [
            InlineKeyboardButton(
                "🌐 Broadcast Both",
                callback_data="broadcast_both",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="broadcast_cancel",
            ),
        ],
    ]

    await message.reply_text(
        "📢 *Broadcast ready.*\n\n"
        "Choose the destination:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return BROADCAST_CONTENT


# =========================================================
# EXECUTE BROADCAST
# =========================================================

async def execute_broadcast(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    user = query.from_user

    if not is_admin(user.id):
        return ConversationHandler.END

    message_id = context.user_data.get(
        "broadcast_message_id"
    )

    source_chat_id = context.user_data.get(
        "broadcast_chat_id"
    )

    if not message_id or not source_chat_id:
        await query.edit_message_text(
            "❌ Broadcast content expired. "
            "Please start /broadcast again."
        )

        return ConversationHandler.END

    choice = query.data

    targets = []

    if choice in (
        "broadcast_users",
        "broadcast_both",
    ):
        users = await get_users()

        for user_id in users:
            targets.append(
                (
                    user_id,
                    "user",
                )
            )

    if choice in (
        "broadcast_groups",
        "broadcast_both",
    ):
        groups = await get_groups()

        for group_id in groups:
            targets.append(
                (
                    group_id,
                    "group",
                )
            )

    if not targets:
        await query.edit_message_text(
            "⚠️ No broadcast targets found."
        )

        return ConversationHandler.END

    await query.edit_message_text(
        "📤 Broadcast started...\n\n"
        f"Targets: {len(targets)}"
    )

    bot = context.bot

    sent = 0
    failed = 0

    for target_id, target_type in targets:

        try:
            await bot.copy_message(
                chat_id=target_id,
                from_chat_id=source_chat_id,
                message_id=message_id,
            )

            sent += 1

        except Exception as exc:
            failed += 1

            print(
                "Broadcast failed for "
                f"{target_type} {target_id}: "
                f"{exc}"
            )

        await asyncio.sleep(0.05)

    await bot.send_message(
        chat_id=user.id,
        text=(
            "✅ *Broadcast completed*\n\n"
            f"📨 Sent: {sent}\n"
            f"⚠️ Failed: {failed}\n"
            f"🎯 Total: {len(targets)}"
        ),
        parse_mode="Markdown",
    )

    context.user_data.pop(
        "broadcast_message_id",
        None,
    )

    context.user_data.pop(
        "broadcast_chat_id",
        None,
    )

    return ConversationHandler.END


# =========================================================
# CANCEL BROADCAST
# =========================================================

async def cancel_broadcast(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    context.user_data.pop(
        "broadcast_message_id",
        None,
    )

    context.user_data.pop(
        "broadcast_chat_id",
        None,
    )

    await query.edit_message_text(
        "❌ Broadcast cancelled."
    )

    return ConversationHandler.END


# =========================================================
# DISCOUNT COMMAND
# =========================================================

async def discount_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    command = update.message.text.split()[0]

    duration_map = {
        "/discount2h": 2,
        "/discount6h": 6,
        "/discount12h": 12,
        "/discount24h": 24,
    }

    duration = duration_map.get(
        command.lower()
    )

    if duration is None:
        await update.message.reply_text(
            "❌ Invalid discount command."
        )

        return ConversationHandler.END

    context.user_data[
        "discount_duration"
    ] = duration

    await update.message.reply_text(
        f"🏷️ *{duration}-Hour Discount*\n\n"
        "Send the discounted USDT price.\n\n"
        "Example:\n"
        "`95`\n\n"
        "The campaign will remain active "
        "for 7 days.",
        parse_mode="Markdown",
    )

    context.user_data[
        "discount_waiting_price"
    ] = True

    return DISCOUNT_CONTENT


# =========================================================
# DISCOUNT PRICE
# =========================================================

async def discount_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return ConversationHandler.END

    text = update.message.text.strip()

    try:
        price = Decimal(text)
    except InvalidOperation:
        await update.message.reply_text(
            "❌ Invalid price.\n\n"
            "Send only the USDT amount.\n"
            "Example: `95`",
            parse_mode="Markdown",
        )

        return DISCOUNT_CONTENT

    if price <= 0:
        await update.message.reply_text(
            "❌ Price must be greater than zero."
        )

        return DISCOUNT_CONTENT

    duration = context.user_data.get(
        "discount_duration"
    )

    if not duration:
        await update.message.reply_text(
            "❌ Discount session expired."
        )

        return ConversationHandler.END

    context.user_data[
        "discount_price"
    ] = price

    context.user_data[
        "discount_waiting_price"
    ] = False

    await update.message.reply_text(
        "📝 Now send the discount announcement.\n\n"
        "You can send:\n"
        "• Text\n"
        "• Photo + caption\n"
        "• Video + caption\n"
        "• GIF/Animation\n\n"
        "This will be used as the promotional "
        "announcement.",
    )

    return DISCOUNT_CONTENT


# =========================================================
# DISCOUNT PROMO CONTENT
# =========================================================

async def discount_promo_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return ConversationHandler.END

    if context.user_data.get(
        "discount_waiting_price"
    ):
        return await discount_price(
            update,
            context,
        )

    message = update.message

    if not message:
        return DISCOUNT_CONTENT

    duration = context.user_data.get(
        "discount_duration"
    )

    price = context.user_data.get(
        "discount_price"
    )

    if not duration or price is None:
        await message.reply_text(
            "❌ Discount session expired."
        )

        return ConversationHandler.END

    media_type = None
    media_id = None
    promo_text = message.text

    if message.photo:
        media_type = "photo"
        media_id = message.photo[-1].file_id
        promo_text = message.caption

    elif message.video:
        media_type = "video"
        media_id = message.video.file_id
        promo_text = message.caption

    elif message.animation:
        media_type = "animation"
        media_id = message.animation.file_id
        promo_text = message.caption

    elif message.document:
        media_type = "document"
        media_id = message.document.file_id
        promo_text = message.caption

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(days=7)
    )

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO discounts (
                duration_hours,
                discount_price,
                active,
                promo_text,
                promo_media_type,
                promo_media_id,
                starts_at,
                expires_at,
                updated_at
            )
            VALUES (
                $1,
                $2,
                TRUE,
                $3,
                $4,
                $5,
                NOW(),
                $6,
                NOW()
            )
            ON CONFLICT (duration_hours)
            DO UPDATE SET
                discount_price =
                    EXCLUDED.discount_price,
                active = TRUE,
                promo_text =
                    EXCLUDED.promo_text,
                promo_media_type =
                    EXCLUDED.promo_media_type,
                promo_media_id =
                    EXCLUDED.promo_media_id,
                starts_at =
                    EXCLUDED.starts_at,
                expires_at =
                    EXCLUDED.expires_at,
                updated_at = NOW()
            """,
            duration,
            price,
            promo_text,
            media_type,
            media_id,
            expires_at,
        )

    context.user_data[
        "discount_message_id"
    ] = message.message_id

    context.user_data[
        "discount_chat_id"
    ] = message.chat_id

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Publish Discount",
                callback_data="discount_publish",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="discount_cancel",
            ),
        ],
    ]

    await message.reply_text(
        "🏷️ *Discount prepared*\n\n"
        f"⏱ Duration: {duration} hours\n"
        f"💵 Price: {price} USDT\n"
        "📅 Campaign: 7 days\n\n"
        "Publish this discount announcement?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return DISCOUNT_CONTENT


# =========================================================
# PUBLISH DISCOUNT
# =========================================================

async def publish_discount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    duration = context.user_data.get(
        "discount_duration"
    )

    if not duration:
        await query.edit_message_text(
            "❌ Discount session expired."
        )

        return ConversationHandler.END

    users = await get_users()
    groups = await get_groups()

    message_id = context.user_data.get(
        "discount_message_id"
    )

    source_chat_id = context.user_data.get(
        "discount_chat_id"
    )

    if not message_id or not source_chat_id:
        await query.edit_message_text(
            "❌ Discount content expired."
        )

        return ConversationHandler.END

    await query.edit_message_text(
        "📢 Publishing discount announcement..."
    )

    sent = 0
    failed = 0

    targets = users + groups

    for target_id in targets:

        try:
            await context.bot.copy_message(
                chat_id=target_id,
                from_chat_id=source_chat_id,
                message_id=message_id,
            )

            sent += 1

        except Exception as exc:
            failed += 1

            print(
                "Discount broadcast failed "
                f"for {target_id}: {exc}"
            )

        await asyncio.sleep(0.05)

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            "✅ *Discount campaign published*\n\n"
            f"⏱ Duration: {duration} hours\n"
            f"📅 Active for: 7 days\n"
            f"📨 Sent: {sent}\n"
            f"⚠️ Failed: {failed}"
        ),
        parse_mode="Markdown",
    )

    context.user_data.pop(
        "discount_duration",
        None,
    )

    context.user_data.pop(
        "discount_price",
        None,
    )

    context.user_data.pop(
        "discount_message_id",
        None,
    )

    context.user_data.pop(
        "discount_chat_id",
        None,
    )

    context.user_data.pop(
        "discount_waiting_price",
        None,
    )

    return ConversationHandler.END


# =========================================================
# CANCEL DISCOUNT
# =========================================================

async def cancel_discount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    context.user_data.pop(
        "discount_duration",
        None,
    )

    context.user_data.pop(
        "discount_price",
        None,
    )

    context.user_data.pop(
        "discount_message_id",
        None,
    )

    context.user_data.pop(
        "discount_chat_id",
        None,
    )

    context.user_data.pop(
        "discount_waiting_price",
        None,
    )

    await query.edit_message_text(
        "❌ Discount cancelled."
    )

    return ConversationHandler.END


# =========================================================
# DISCOUNT CONVERSATION
# =========================================================

def build_discount_conversation():
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "discount2h",
                discount_start,
            ),
            CommandHandler(
                "discount6h",
                discount_start,
            ),
            CommandHandler(
                "discount12h",
                discount_start,
            ),
            CommandHandler(
                "discount24h",
                discount_start,
            ),
        ],
        states={
            DISCOUNT_CONTENT: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    discount_price,
                ),
                MessageHandler(
                    (
                        filters.PHOTO
                        | filters.VIDEO
                        | filters.ANIMATION
                        | filters.Document.ALL
                    )
                    & ~filters.COMMAND,
                    discount_promo_content,
                ),
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel_discount_command,
            ),
        ],
        allow_reentry=True,
    )


async def cancel_discount_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Operation cancelled."
    )

    return ConversationHandler.END


# =========================================================
# BROADCAST CONVERSATION
# =========================================================

def build_broadcast_conversation():
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "broadcast",
                broadcast_start,
            ),
        ],
        states={
            BROADCAST_CONTENT: [
                CallbackQueryHandler(
                    execute_broadcast,
                    pattern=(
                        r"^broadcast_users$"
                        r"|^broadcast_groups$"
                        r"|^broadcast_both$"
                    ),
                ),
                CallbackQueryHandler(
                    cancel_broadcast,
                    pattern=r"^broadcast_cancel$",
                ),
                MessageHandler(
                    (
                        filters.TEXT
                        | filters.PHOTO
                        | filters.VIDEO
                        | filters.ANIMATION
                        | filters.Document.ALL
                    )
                    & ~filters.COMMAND,
                    broadcast_content,
                ),
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel_broadcast_command,
            ),
        ],
        allow_reentry=True,
    )


async def cancel_broadcast_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop(
        "broadcast_message_id",
        None,
    )

    context.user_data.pop(
        "broadcast_chat_id",
        None,
    )

    await update.message.reply_text(
        "❌ Broadcast cancelled."
    )

    return ConversationHandler.END
