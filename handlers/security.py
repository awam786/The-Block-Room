from telegram import Update
from telegram.ext import ContextTypes

from utils.security import (
    check_user_rate_limit,
    audit_log,
)


async def security_guard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Lightweight global protection layer.

    It does not block normal users aggressively.
    It only stops excessive repeated updates.
    """

    user = update.effective_user

    if not user:
        return

    allowed = check_user_rate_limit(
        user.id,
        "global",
    )

    if not allowed:
        await audit_log(
            action="rate_limit",
            user_id=user.id,
            group_id=(
                update.effective_chat.id
                if update.effective_chat
                else None
            ),
            details="Global rate limit triggered.",
        )

        if update.message:
            await update.message.reply_text(
                "⚠️ You are sending messages "
                "too quickly.\n\n"
                "Please wait a moment."
            )

        return


async def security_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user:
        return

    await update.message.reply_text(
        "🔐 Security protection is active.\n\n"
        "• Rate limiting: ON\n"
        "• Admin protection: ON\n"
        "• Secret filtering: ON\n"
        "• Audit logging: ON"
    )
