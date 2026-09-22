from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def admin_only(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    user = update.effective_user

    if not user or not is_admin(user.id):
        if update.message:
            await update.message.reply_text(
                "⛔ You don't have permission to use this command."
            )
        return False

    return True


async def admin_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await admin_only(update, context):
        return

    await update.message.reply_text(
        "🛠️ THE BLOCK ROOM — ADMIN PANEL\n\n"
        "📊 /status — Bot statistics & system status\n"
        "💰 /2hour — Set 2-hour price\n"
        "💰 /6hours — Set 6-hour price\n"
        "💰 /12hours — Set 12-hour price\n"
        "💰 /24hours — Set 24-hour price\n\n"
        "💵 /BNB — Set BNB USDT wallet\n"
        "💵 /ETH — Set Ethereum USDT wallet\n"
        "💵 /SOL — Set Solana USDT wallet\n\n"
        "🏷️ /discount2h — Set 2-hour discount\n"
        "🏷️ /discount6h — Set 6-hour discount\n"
        "🏷️ /discount12h — Set 12-hour discount\n"
        "🏷️ /discount24h — Set 24-hour discount\n\n"
        "🤖 /Activebuybot — Activate BuyBot\n"
        "🤖 /removebuybot — Disable BuyBot\n\n"
        "📢 /broadcast — Start broadcast\n"
        "🎫 /tickets — Support tickets"
    )
