from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from config import BOT_TOKEN


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await update.message.reply_text(
        f"👋 Welcome {user.first_name}!\n\n"
        "🏛️ Welcome to The Block Room.\n\n"
        "Use /trend to list a token on trending.\n"
        "Use /help to see available commands."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🏛️ The Block Room\n\n"
        "Available commands:\n\n"
        "/start — Start the bot\n"
        "/trend — List a token on trending\n"
        "/help — Show help"
    )


def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    print("The Block Room is starting...")

    application.run_polling()


if __name__ == "__main__":
    main()
