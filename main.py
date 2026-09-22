from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN

from database.connection import (
    init_db,
    close_db,
)

from handlers.start import start_command

from handlers.admin import (
    admin_help,
)

from handlers.pricing import (
    set_price,
    show_prices,
)

from handlers.wallets import (
    set_wallet,
    remove_wallet,
    show_wallets,
)

from handlers.trending import (
    trend_start,
    chain_selected,
    contract_received,
    duration_selected,
    trend_cancel,
    SELECT_CHAIN,
    ENTER_CONTRACT,
    SELECT_DURATION,
)


# ============================================================
# DATABASE LIFECYCLE
# ============================================================

async def post_init(
    application: Application,
):
    await init_db()

    print(
        "PostgreSQL connected successfully."
    )


async def post_shutdown(
    application: Application,
):
    await close_db()

    print(
        "PostgreSQL connection closed."
    )


# ============================================================
# HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🏛️ *THE BLOCK ROOM*\n\n"
        "📈 /trend — List a token on trending\n"
        "💬 /support — Contact support\n"
        "❓ /help — Show help",
        parse_mode="Markdown",
    )


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # --------------------------------------------------------
    # BASIC COMMANDS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "adminhelp",
            admin_help,
        )
    )

    # --------------------------------------------------------
    # PRICING
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            [
                "2hour",
                "6hours",
                "12hours",
                "24hours",
            ],
            set_price,
        )
    )

    application.add_handler(
        CommandHandler(
            "prices",
            show_prices,
        )
    )

    # --------------------------------------------------------
    # PAYMENT WALLETS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            [
                "BNB",
                "ETH",
                "SOL",
            ],
            set_wallet,
        )
    )

    application.add_handler(
        CommandHandler(
            [
                "removeBNB",
                "removeETH",
                "removeSOL",
            ],
            remove_wallet,
        )
    )

    application.add_handler(
        CommandHandler(
            "wallets",
            show_wallets,
        )
    )

    # --------------------------------------------------------
    # TRENDING CONVERSATION
    # --------------------------------------------------------

    trend_conversation = ConversationHandler(

        entry_points=[
            CommandHandler(
                "trend",
                trend_start,
            )
        ],

        states={

            SELECT_CHAIN: [
                CallbackQueryHandler(
                    chain_selected,
                    pattern=r"^trend_chain_|^trend_cancel$",
                )
            ],

            ENTER_CONTRACT: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    contract_received,
                )
            ],

            SELECT_DURATION: [
                CallbackQueryHandler(
                    duration_selected,
                    pattern=r"^trend_duration_|^trend_cancel_duration$",
                )
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                trend_cancel,
            )
        ],

        allow_reentry=True,
    )

    application.add_handler(
        trend_conversation
    )

    # --------------------------------------------------------
    # START BOT
    # --------------------------------------------------------

    print(
        "The Block Room is starting..."
    )

    application.run_polling()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
