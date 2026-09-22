import re
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import (
    ADMIN_IDS,
    ADMIN_GROUP_ID,
)

from database.connection import (
    get_pool,
)


# ============================================================
# CONVERSATION STATES
# ============================================================

SUPPORT_MESSAGE = 1


# ============================================================
# TICKET ID
# ============================================================

TICKET_PATTERN = re.compile(
    r"^TK-\d+$",
    re.IGNORECASE,
)


async def generate_ticket_id(
    connection,
) -> str:
    row = await connection.fetchrow(
        """
        SELECT
            COALESCE(
                MAX(id),
                0
            ) + 1 AS next_number
        FROM tickets
        """
    )

    number = int(
        row["next_number"]
    )

    return f"TK-{number:06d}"


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(
    user_id: int | None,
) -> bool:
    if user_id is None:
        return False

    return user_id in ADMIN_IDS


# ============================================================
# SUPPORT START
# ============================================================

async def support_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return ConversationHandler.END

    if not update.effective_chat:
        return ConversationHandler.END

    # --------------------------------------------------------
    # Admin group handling
    # --------------------------------------------------------

    if (
        ADMIN_GROUP_ID
        and str(update.effective_chat.id)
        == str(ADMIN_GROUP_ID)
    ):
        await update.message.reply_text(
            "🛠️ Admin support commands:\n\n"
            "/reply TK-000001 Your message\n"
            "/closeticket TK-000001\n"
            "/ticket TK-000001\n"
            "/tickets\n"
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "🎫 *Support Ticket*\n\n"
        "Please describe your issue in one message.\n\n"
        "You can include the relevant details "
        "needed to understand the problem.\n\n"
        "Type /cancel to cancel.",
        parse_mode="Markdown",
    )

    return SUPPORT_MESSAGE


# ============================================================
# SUPPORT MESSAGE
# ============================================================

async def support_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return ConversationHandler.END

    if not update.message:
        return ConversationHandler.END

    user = update.effective_user

    description = (
        update.message.text
        or ""
    ).strip()

    if not description:
        await update.message.reply_text(
            "Please describe your issue in text."
        )

        return SUPPORT_MESSAGE

    if len(description) > 5000:
        await update.message.reply_text(
            "Your message is too long.\n\n"
            "Please keep the support description "
            "under 5,000 characters."
        )

        return SUPPORT_MESSAGE

    pool = await get_pool()

    async with pool.acquire() as connection:

        # ----------------------------------------------------
        # Register / update user
        # ----------------------------------------------------

        await connection.execute(
            """
            INSERT INTO users (
                telegram_id,
                username,
                first_name,
                last_name,
                updated_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                NOW()
            )
            ON CONFLICT (
                telegram_id
            )
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

        # ----------------------------------------------------
        # Check existing open ticket
        # ----------------------------------------------------

        existing = await connection.fetchrow(
            """
            SELECT
                ticket_id,
                status
            FROM tickets
            WHERE user_id = $1
              AND status IN (
                  'OPEN',
                  'IN_PROGRESS'
              )
            ORDER BY id DESC
            LIMIT 1
            """,
            user.id,
        )

        if existing:
            await update.message.reply_text(
                "🎫 You already have an active "
                "support ticket.\n\n"
                f"Ticket: `{existing['ticket_id']}`\n"
                f"Status: `{existing['status']}`\n\n"
                "Please continue in the same ticket "
                "instead of creating another one.",
                parse_mode="Markdown",
            )

            return ConversationHandler.END

        # ----------------------------------------------------
        # Create ticket
        # ----------------------------------------------------

        ticket_id = await generate_ticket_id(
            connection
        )

        subject = description[:120]

        await connection.execute(
            """
            INSERT INTO tickets (
                ticket_id,
                user_id,
                status,
                subject,
                description
            )
            VALUES (
                $1,
                $2,
                'OPEN',
                $3,
                $4
            )
            """,
            ticket_id,
            user.id,
            subject,
            description,
        )

    # --------------------------------------------------------
    # User confirmation
    # --------------------------------------------------------

    await update.message.reply_text(
        "🎫 *Support ticket created*\n\n"
        f"Ticket ID: `{ticket_id}`\n"
        "Status: `OPEN`\n\n"
        "Your request has been forwarded to "
        "the support team for review.\n\n"
        "You can use /ticket to check its status.",
        parse_mode="Markdown",
    )

    # --------------------------------------------------------
    # Forward to admin group
    # --------------------------------------------------------

    await forward_ticket_to_admin(
        update=update,
        ticket_id=ticket_id,
        description=description,
    )

    return ConversationHandler.END


# ============================================================
# FORWARD TICKET TO ADMIN
# ============================================================

async def forward_ticket_to_admin(
    update: Update,
    ticket_id: str,
    description: str,
):
    if not ADMIN_GROUP_ID:
        print(
            "ADMIN_GROUP_ID is not configured."
        )
        return

    user = update.effective_user

    username = (
        f"@{user.username}"
        if user.username
        else "No username"
    )

    name = (
        user.full_name
        if user
        else "Unknown user"
    )

    text = (
        "🎫 NEW SUPPORT TICKET\n\n"
        f"Ticket: {ticket_id}\n"
        f"User: {name}\n"
        f"Username: {username}\n"
        f"Telegram ID: {user.id}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{description}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Reply:\n"
        f"/reply {ticket_id} Your message\n\n"
        "Close:\n"
        f"/closeticket {ticket_id}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💬 Reply",
                    callback_data=(
                        f"support_reply:{ticket_id}"
                    ),
                ),
                InlineKeyboardButton(
                    "🔒 Close",
                    callback_data=(
                        f"support_close:{ticket_id}"
                    ),
                ),
            ]
        ]
    )

    try:
        await update.get_bot().send_message(
            chat_id=int(
                ADMIN_GROUP_ID
            ),
            text=text,
            reply_markup=keyboard,
        )

    except Exception as exc:
        print(
            "Failed to forward support ticket:",
            exc,
        )


# ============================================================
# TICKET STATUS
# ============================================================

async def ticket_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    args = context.args

    if not args:
        await update.message.reply_text(
            "Use:\n"
            "/ticket TK-000001"
        )

        return

    ticket_id = args[0].upper()

    if not TICKET_PATTERN.match(
        ticket_id
    ):
        await update.message.reply_text(
            "Invalid ticket ID.\n\n"
            "Example:\n"
            "/ticket TK-000001"
        )

        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        ticket = await connection.fetchrow(
            """
            SELECT
                ticket_id,
                user_id,
                status,
                subject,
                admin_message,
                created_at,
                updated_at,
                closed_at
            FROM tickets
            WHERE ticket_id = $1
            """,
            ticket_id,
        )

    if not ticket:
        await update.message.reply_text(
            "❌ Ticket not found."
        )

        return

    user_id = update.effective_user.id

    if (
        ticket["user_id"] != user_id
        and not is_admin(user_id)
    ):
        await update.message.reply_text(
            "❌ You don't have access to this ticket."
        )

        return

    status = ticket[
        "status"
    ]

    response = (
        "🎫 *Ticket Status*\n\n"
        f"ID: `{ticket['ticket_id']}`\n"
        f"Status: `{status}`\n"
    )

    if ticket["admin_message"]:
        response += (
            "\n💬 *Latest support response:*\n"
            f"{ticket['admin_message']}\n"
        )

    if ticket["closed_at"]:
        response += (
            "\n🔒 Ticket closed."
        )

    await update.message.reply_text(
        response,
        parse_mode="Markdown",
    )


# ============================================================
# USER TICKETS
# ============================================================

async def my_tickets(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        tickets = await connection.fetch(
            """
            SELECT
                ticket_id,
                status,
                subject,
                created_at,
                closed_at
            FROM tickets
            WHERE user_id = $1
            ORDER BY id DESC
            LIMIT 10
            """,
            update.effective_user.id,
        )

    if not tickets:
        await update.message.reply_text(
            "You don't have any support tickets yet."
        )

        return

    lines = [
        "🎫 *Your Recent Tickets*\n"
    ]

    for ticket in tickets:
        lines.append(
            f"• `{ticket['ticket_id']}` — "
            f"{ticket['status']}"
        )

    lines.append(
        "\nUse `/ticket TK-000001` "
        "to view a ticket."
    )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# ============================================================
# ADMIN REPLY
# ============================================================

async def admin_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    if not is_admin(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "❌ Admin access required."
        )

        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Use:\n"
            "/reply TK-000001 Your message"
        )

        return

    ticket_id = (
        context.args[0]
        .upper()
    )

    message = " ".join(
        context.args[1:]
    ).strip()

    if not TICKET_PATTERN.match(
        ticket_id
    ):
        await update.message.reply_text(
            "Invalid ticket ID."
        )

        return

    if not message:
        await update.message.reply_text(
            "Please provide a reply message."
        )

        return

    await send_admin_reply(
        update,
        ticket_id,
        message,
    )


# ============================================================
# SEND ADMIN REPLY
# ============================================================

async def send_admin_reply(
    update: Update,
    ticket_id: str,
    message: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        ticket = await connection.fetchrow(
            """
            SELECT
                ticket_id,
                user_id,
                status
            FROM tickets
            WHERE ticket_id = $1
            """,
            ticket_id,
        )

        if not ticket:
            await update.message.reply_text(
                "❌ Ticket not found."
            )

            return

        if ticket["status"] == "CLOSED":
            await update.message.reply_text(
                "🔒 This ticket is already closed."
            )

            return

        # ----------------------------------------------------
        # Store response and close ticket
        # ----------------------------------------------------

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
            message,
            ticket_id,
        )

    # --------------------------------------------------------
    # Send response to user
    # --------------------------------------------------------

    try:
        await update.get_bot().send_message(
            chat_id=ticket["user_id"],
            text=(
                "💬 *Support Response*\n\n"
                f"Ticket: `{ticket_id}`\n\n"
                f"{message}\n\n"
                "🔒 This ticket has been closed.\n\n"
                "If you have a new issue, use /support "
                "to create a new ticket."
            ),
            parse_mode="Markdown",
        )

        await update.message.reply_text(
            f"✅ Reply sent.\n"
            f"🎫 {ticket_id}\n"
            "🔒 Ticket closed."
        )

    except Exception as exc:
        print(
            "Failed to send admin reply:",
            exc,
        )

        await update.message.reply_text(
            "⚠️ The reply was saved, but Telegram "
            "could not deliver it to the user."
        )


# ============================================================
# ADMIN CLOSE TICKET
# ============================================================

async def close_ticket(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    if not is_admin(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "❌ Admin access required."
        )

        return

    if not context.args:
        await update.message.reply_text(
            "Use:\n"
            "/closeticket TK-000001"
        )

        return

    ticket_id = (
        context.args[0]
        .upper()
    )

    if not TICKET_PATTERN.match(
        ticket_id
    ):
        await update.message.reply_text(
            "Invalid ticket ID."
        )

        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        ticket = await connection.fetchrow(
            """
            SELECT
                ticket_id,
                user_id,
                status
            FROM tickets
            WHERE ticket_id = $1
            """,
            ticket_id,
        )

        if not ticket:
            await update.message.reply_text(
                "❌ Ticket not found."
            )

            return

        if ticket["status"] == "CLOSED":
            await update.message.reply_text(
                "This ticket is already closed."
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

    try:
        await update.get_bot().send_message(
            chat_id=ticket["user_id"],
            text=(
                "🔒 *Support Ticket Closed*\n\n"
                f"Ticket: `{ticket_id}`\n\n"
                "Your support ticket has been closed.\n"
                "If you have another issue, use /support "
                "to create a new ticket."
            ),
            parse_mode="Markdown",
        )

    except Exception as exc:
        print(
            "Ticket closure notification error:",
            exc,
        )

    await update.message.reply_text(
        f"🔒 Ticket `{ticket_id}` closed.",
        parse_mode="Markdown",
    )


# ============================================================
# ADMIN TICKET LIST
# ============================================================

async def admin_tickets(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    if not is_admin(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "❌ Admin access required."
        )

        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        tickets = await connection.fetch(
            """
            SELECT
                ticket_id,
                user_id,
                status,
                subject,
                created_at
            FROM tickets
            WHERE status != 'CLOSED'
            ORDER BY id ASC
            LIMIT 50
            """
        )

    if not tickets:
        await update.message.reply_text(
            "✅ No open support tickets."
        )

        return

    lines = [
        "🎫 *Open Support Tickets*\n"
    ]

    for ticket in tickets:
        lines.append(
            f"• `{ticket['ticket_id']}` — "
            f"{ticket['status']}\n"
            f"  User: `{ticket['user_id']}`"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# ============================================================
# CALLBACK BUTTONS
# ============================================================

async def support_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = query.from_user

    if not is_admin(
        user.id
    ):
        await query.answer(
            "Admin access required.",
            show_alert=True,
        )

        return

    data = query.data or ""

    if data.startswith(
        "support_close:"
    ):
        ticket_id = data.split(
            ":",
            1,
        )[1]

        await close_ticket_by_callback(
            query,
            ticket_id,
        )

        return

    if data.startswith(
        "support_reply:"
    ):
        ticket_id = data.split(
            ":",
            1,
        )[1]

        await query.message.reply_text(
            "💬 Reply to this ticket with:\n\n"
            f"/reply {ticket_id} Your message"
        )

        return


# ============================================================
# CALLBACK CLOSE
# ============================================================

async def close_ticket_by_callback(
    query,
    ticket_id: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        ticket = await connection.fetchrow(
            """
            SELECT
                ticket_id,
                user_id,
                status
            FROM tickets
            WHERE ticket_id = $1
            """,
            ticket_id,
        )

        if not ticket:
            await query.answer(
                "Ticket not found.",
                show_alert=True,
            )

            return

        if ticket["status"] == "CLOSED":
            await query.answer(
                "Already closed.",
                show_alert=True,
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

    try:
        await query.get_bot().send_message(
            chat_id=ticket["user_id"],
            text=(
                "🔒 *Support Ticket Closed*\n\n"
                f"Ticket: `{ticket_id}`\n\n"
                "Your support request has been closed.\n"
                "Use /support if you need help with "
                "a new issue."
            ),
            parse_mode="Markdown",
        )
    except Exception as exc:
        print(
            "Ticket closure notification error:",
            exc,
        )

    try:
        await query.edit_message_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    try:
        await query.message.reply_text(
            f"🔒 `{ticket_id}` closed.",
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ============================================================
# CANCEL
# ============================================================

async def support_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message:
        await update.message.reply_text(
            "❌ Support request cancelled."
        )

    return ConversationHandler.END


# ============================================================
# BUILD SUPPORT CONVERSATION
# ============================================================

def build_support_conversation():
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "support",
                support_start,
            ),
        ],
        states={
            SUPPORT_MESSAGE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    support_message,
                ),
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                support_cancel,
            ),
        ],
        allow_reentry=True,
    )


# ============================================================
# REGULAR HANDLERS
# ============================================================

def support_handlers():
    return [
        CommandHandler(
            "ticket",
            ticket_status,
        ),
        CommandHandler(
            "mytickets",
            my_tickets,
        ),
        CommandHandler(
            "reply",
            admin_reply,
        ),
        CommandHandler(
            "closeticket",
            close_ticket,
        ),
        CommandHandler(
            "tickets",
            admin_tickets,
        ),
        CallbackQueryHandler(
            support_callback,
            pattern=r"^support_(reply|close):",
        ),
    ]


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "SUPPORT_MESSAGE",
    "support_start",
    "support_message",
    "ticket_status",
    "my_tickets",
    "admin_reply",
    "close_ticket",
    "admin_tickets",
    "support_callback",
    "support_cancel",
    "build_support_conversation",
    "support_handlers",
]
