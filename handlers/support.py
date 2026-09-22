import os

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

from config import ADMIN_IDS, ADMIN_GROUP_ID
from database.connection import get_pool


# =========================================================
# STATE
# =========================================================

SUPPORT_DESCRIPTION = 1


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# =========================================================
# TICKET ID
# =========================================================

async def generate_ticket_id():
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            INSERT INTO tickets (
                ticket_id,
                status
            )
            VALUES (
                'TEMP',
                'TEMP'
            )
            RETURNING id
            """
        )

        ticket_number = row["id"]

        ticket_id = (
            f"TK-{ticket_number:06d}"
        )

        await connection.execute(
            """
            UPDATE tickets
            SET
                ticket_id = $1,
                status = 'OPEN'
            WHERE id = $2
            """,
            ticket_id,
            ticket_number,
        )

    return ticket_id


# =========================================================
# SUPPORT START
# =========================================================

async def support_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user:
        return ConversationHandler.END

    await update.message.reply_text(
        "🛠️ *Support*\n\n"
        "Please describe your issue in detail.\n\n"
        "If this is about a transaction, include "
        "the transaction hash, token, network, "
        "amount, and any other useful details.",
        parse_mode="Markdown",
    )

    return SUPPORT_DESCRIPTION


# =========================================================
# CREATE TICKET
# =========================================================

async def create_ticket(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    message = update.message

    if not user or not message:
        return ConversationHandler.END

    description = message.text.strip()

    if not description:
        await message.reply_text(
            "❌ Please describe your issue."
        )

        return SUPPORT_DESCRIPTION

    ticket_id = await generate_ticket_id()

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE tickets
            SET
                user_id = $1,
                status = 'OPEN',
                subject = $2,
                description = $3,
                updated_at = NOW()
            WHERE ticket_id = $4
            """,
            user.id,
            "Customer Support",
            description,
            ticket_id,
        )

    await message.reply_text(
        "🎫 *Support ticket created*\n\n"
        f"Ticket ID: `{ticket_id}`\n\n"
        "Your request has been forwarded to "
        "the support team.\n\n"
        "Please keep this ticket ID for reference.",
        parse_mode="Markdown",
    )

    await forward_ticket_to_admin(
        update,
        ticket_id,
        description,
    )

    return ConversationHandler.END


# =========================================================
# FORWARD TO ADMIN GROUP
# =========================================================

async def forward_ticket_to_admin(
    update: Update,
    ticket_id: str,
    description: str,
):
    user = update.effective_user

    if not ADMIN_GROUP_ID:
        print(
            "ADMIN_GROUP_ID is not configured."
        )

        return

    try:
        admin_group_id = int(
            ADMIN_GROUP_ID
        )
    except ValueError:
        print(
            "ADMIN_GROUP_ID is invalid."
        )

        return

    username = (
        f"@{user.username}"
        if user.username
        else "No username"
    )

    text = (
        "🎫 *NEW SUPPORT TICKET*\n\n"
        f"ID: `{ticket_id}`\n"
        f"User ID: `{user.id}`\n"
        f"User: {username}\n"
        f"Name: {user.full_name}\n\n"
        "📝 *Issue:*\n"
        f"{description}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "💬 Reply",
                callback_data=(
                    f"ticket_reply:{ticket_id}"
                ),
            ),
            InlineKeyboardButton(
                "🔒 Close",
                callback_data=(
                    f"ticket_close:{ticket_id}"
                ),
            ),
        ],
    ]

    from telegram import Bot

    from config import BOT_TOKEN

    bot = Bot(BOT_TOKEN)

    await bot.send_message(
        chat_id=admin_group_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# =========================================================
# TICKET STATUS
# =========================================================

async def ticket_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user:
        return

    args = context.args

    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "`/ticket TK-000001`",
            parse_mode="Markdown",
        )

        return

    ticket_id = args[0].upper()

    pool = await get_pool()

    async with pool.acquire() as connection:
        ticket = await connection.fetchrow(
            """
            SELECT
                ticket_id,
                status,
                subject,
                description,
                admin_message,
                created_at,
                updated_at,
                closed_at
            FROM tickets
            WHERE ticket_id = $1
              AND user_id = $2
            """,
            ticket_id,
            user.id,
        )

    if not ticket:
        await update.message.reply_text(
            "❌ Ticket not found."
        )

        return

    status = ticket["status"]

    status_text = {
        "OPEN": "🟡 Open",
        "IN_PROGRESS": "🔵 In Progress",
        "CLOSED": "⚪ Closed",
        "SOLVED": "🟢 Solved",
    }.get(
        status,
        status,
    )

    text = (
        "🎫 *Ticket Status*\n\n"
        f"ID: `{ticket['ticket_id']}`\n"
        f"Status: {status_text}\n"
        f"Created: {ticket['created_at']}\n"
    )

    if ticket["admin_message"]:
        text += (
            "\n💬 *Admin response:*\n"
            f"{ticket['admin_message']}\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN REPLY BUTTON
# =========================================================

async def admin_ticket_reply_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    user = query.from_user

    if not is_admin(user.id):
        await query.answer(
            "Not authorized.",
            show_alert=True,
        )

        return

    ticket_id = query.data.split(
        ":",
        1,
    )[1]

    context.user_data[
        "reply_ticket_id"
    ] = ticket_id

    await query.message.reply_text(
        f"💬 Replying to `{ticket_id}`\n\n"
        "Send your reply now.",
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN REPLY MESSAGE
# =========================================================

async def admin_reply_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    ticket_id = context.user_data.get(
        "reply_ticket_id"
    )

    if not ticket_id:
        return

    message = update.message

    if not message:
        return

    reply_text = message.text

    if not reply_text:
        await message.reply_text(
            "❌ Please send a text reply."
        )

        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        ticket = await connection.fetchrow(
            """
            SELECT
                user_id,
                status
            FROM tickets
            WHERE ticket_id = $1
            """,
            ticket_id,
        )

    if not ticket:
        await message.reply_text(
            "❌ Ticket not found."
        )

        context.user_data.pop(
            "reply_ticket_id",
            None,
        )

        return

    customer_id = ticket["user_id"]

    if not customer_id:
        await message.reply_text(
            "❌ Customer ID is missing."
        )

        return

    try:
        await context.bot.send_message(
            chat_id=customer_id,
            text=(
                "💬 *Support Response*\n\n"
                f"Ticket: `{ticket_id}`\n\n"
                f"{reply_text}\n\n"
                "Your ticket has been marked as "
                "closed. If you have another "
                "question, please open a new "
                "support request."
            ),
            parse_mode="Markdown",
        )

    except Exception as exc:
        await message.reply_text(
            "❌ Could not send the reply "
            "to the customer.\n\n"
            f"Error: {exc}"
        )

        return

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE tickets
            SET
                status = 'CLOSED',
                admin_message = $1,
                updated_at = NOW(),
                closed_at = NOW()
            WHERE ticket_id = $2
            """,
            reply_text,
            ticket_id,
        )

    await message.reply_text(
        f"✅ Reply sent.\n\n"
        f"🎫 `{ticket_id}` is now closed.",
        parse_mode="Markdown",
    )

    context.user_data.pop(
        "reply_ticket_id",
        None,
    )


# =========================================================
# ADMIN CLOSE
# =========================================================

async def admin_ticket_close(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    user = query.from_user

    if not is_admin(user.id):
        await query.answer(
            "Not authorized.",
            show_alert=True,
        )

        return

    ticket_id = query.data.split(
        ":",
        1,
    )[1]

    pool = await get_pool()

    async with pool.acquire() as connection:
        ticket = await connection.fetchrow(
            """
            SELECT user_id
            FROM tickets
            WHERE ticket_id = $1
            """,
            ticket_id,
        )

        if not ticket:
            await query.edit_message_text(
                "❌ Ticket not found."
            )

            return

        await connection.execute(
            """
            UPDATE tickets
            SET
                status = 'CLOSED',
                updated_at = NOW(),
                closed_at = NOW()
            WHERE ticket_id = $1
            """,
            ticket_id,
        )

    customer_id = ticket["user_id"]

    if customer_id:
        try:
            await context.bot.send_message(
                chat_id=customer_id,
                text=(
                    "🔒 *Support ticket closed*\n\n"
                    f"Ticket: `{ticket_id}`\n\n"
                    "If you still need help, "
                    "please open a new support request."
                ),
                parse_mode="Markdown",
            )
        except Exception as exc:
            print(
                "Could not notify customer: "
                f"{exc}"
            )

    await query.edit_message_text(
        f"🔒 *Ticket closed*\n\n"
        f"ID: `{ticket_id}`",
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN TICKET LIST
# =========================================================

async def admin_tickets(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                ticket_id,
                user_id,
                status,
                created_at
            FROM tickets
            ORDER BY created_at DESC
            LIMIT 20
            """
        )

    if not rows:
        await update.message.reply_text(
            "🎫 No tickets found."
        )

        return

    lines = [
        "🎫 *Recent Tickets*",
        "",
    ]

    for row in rows:
        status = row["status"]

        lines.append(
            f"`{row['ticket_id']}` — "
            f"{status} — "
            f"{row['user_id']}"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN TICKET STATS
# =========================================================

async def ticket_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                status,
                COUNT(*) AS total
            FROM tickets
            GROUP BY status
            """
        )

    counts = {
        "OPEN": 0,
        "IN_PROGRESS": 0,
        "CLOSED": 0,
        "SOLVED": 0,
    }

    for row in rows:
        counts[row["status"]] = row["total"]

    await update.message.reply_text(
        "🎫 *Ticket Statistics*\n\n"
        f"🟡 Open: {counts['OPEN']}\n"
        f"🔵 In Progress: "
        f"{counts['IN_PROGRESS']}\n"
        f"🟢 Solved: {counts['SOLVED']}\n"
        f"⚪ Closed: {counts['CLOSED']}",
        parse_mode="Markdown",
    )


# =========================================================
# SUPPORT CONVERSATION
# =========================================================

def build_support_conversation():
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "support",
                support_start,
            ),
        ],
        states={
            SUPPORT_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    create_ticket,
                ),
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel_support,
            ),
        ],
        allow_reentry=True,
    )


# =========================================================
# CANCEL SUPPORT
# =========================================================

async def cancel_support(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "❌ Support request cancelled."
    )

    return ConversationHandler.END
