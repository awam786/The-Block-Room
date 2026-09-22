import asyncio

from telegram import Update

from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import BOT_TOKEN

from database.connection import (
    init_db,
    close_db,
)

from handlers.start import start_command

from handlers.admin import admin_help

from handlers.pricing import (
    set_price,
    show_prices,
)

from handlers.wallets import (
    set_wallet,
    remove_wallet,
    show_wallets,
)

from handlers.buybot import (
    activate_buybot,
    remove_buybot,
    list_tokens,
    build_buybot_conversation,
)

from handlers.trending import (
    trend_start,
    chain_selected,
    contract_received,
    duration_selected,
    payment_started,
    transaction_hash_received,
    cancel_payment,
    trend_cancel,
    SELECT_CHAIN,
    ENTER_CONTRACT,
    SELECT_DURATION,
    ENTER_TX_HASH,
)

from services.payment_worker import (
    payment_worker,
)

from services.trend_activation_worker import (
    trend_activation_worker,
)

from services.trending_engine import (
    trending_engine_worker,
)

from services.trending_publisher import (
    trending_publisher_worker,
)


payment_worker_task = None
trend_activation_worker_task = None
trending_engine_worker_task = None
trending_publisher_worker_task = None


async def post_init(
    application: Application,
):
    global payment_worker_task
    global trend_activation_worker_task
    global trending_engine_worker_task
    global trending_publisher_worker_task

    await init_db()

    print(
        "PostgreSQL connected successfully."
    )

    payment_worker_task = asyncio.create_task(
        payment_worker()
    )

    print(
        "Payment worker launched."
    )

    trend_activation_worker_task = (
        asyncio.create_task(
            trend_activation_worker()
        )
    )

    print(
        "Trend activation worker launched."
    )

    trending_engine_worker_task = (
        asyncio.create_task(
            trending_engine_worker()
        )
    )

    print(
        "Trending engine launched."
    )

    trending_publisher_worker_task = (
        asyncio.create_task(
            trending_publisher_worker()
        )
    )

    print(
        "Trending publisher launched."
    )


async def post_shutdown(
    application: Application,
):
    global payment_worker_task
    global trend_activation_worker_task
    global trending_engine_worker_task
    global trending_publisher_worker_task

    tasks = [
        payment_worker_task,
        trend_activation_worker_task,
        trending_engine_worker_task,
        trending_publisher_worker_task,
    ]

    for task in tasks:

        if task:
            task.cancel()

    for task in tasks:

        if task:

            try:
                await task

            except asyncio.CancelledError:
                pass

    payment_worker_task = None
    trend_activation_worker_task = None
    trending_engine_worker_task = None
    trending_publisher_worker_task = None

    await close_db()

    print(
        "PostgreSQL connection closed."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🏛️ *THE BLOCK ROOM*\n\n"
        "📈 /trend — List a token on trending\n"
        "💰 /prices — View trending packages\n"
        "🤖 /buybot — Manage BuyBot in a group\n"
        "➕ /add — Add BuyBot token\n"
        "➖ /remove — Remove BuyBot token\n"
        "📋 /tokens — View monitored tokens\n"
        "💬 /support — Contact support\n"
        "❓ /help — Show help",
        parse_mode="Markdown",
    )


def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ========================================================
    # BASIC
    # ========================================================

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

    # ========================================================
    # MAIN ADMIN — BUYBOT ACTIVATION
    # ========================================================

    application.add_handler(
        CommandHandler(
            "activebuybot",
            activate_buybot,
        )
    )

    application.add_handler(
        CommandHandler(
            "removebuybot",
            remove_buybot,
        )
    )

    # ========================================================
    # PRICING
    # ========================================================

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

    # ========================================================
    # PAYMENT WALLETS
    # ========================================================

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

    # ========================================================
    # BUYBOT TOKEN LIST
    # ========================================================

    application.add_handler(
        CommandHandler(
            "tokens",
            list_tokens,
        )
    )

    # ========================================================
    # BUYBOT SETTINGS + TOKEN MANAGEMENT
    #
    # Includes:
    # /buybot
    # /buybotsettings
    # /add
    # /remove
    #
    # And all BuyBot inline menus.
    # ========================================================

    application.add_handler(
        build_buybot_conversation()
    )

    # ========================================================
    # TRENDING CONVERSATION
    # ========================================================

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
                    pattern=(
                        r"^trend_chain_"
                        r"|^trend_cancel$"
                    ),
                ),
            ],

            ENTER_CONTRACT: [

                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    contract_received,
                ),
            ],

            SELECT_DURATION: [

                CallbackQueryHandler(
                    duration_selected,
                    pattern=(
                        r"^trend_duration_"
                        r"|^trend_cancel_duration$"
                    ),
                ),
            ],

            ENTER_TX_HASH: [

                CallbackQueryHandler(
                    payment_started,
                    pattern=r"^trend_paid$",
                ),

                CallbackQueryHandler(
                    cancel_payment,
                    pattern=r"^trend_cancel_payment$",
                ),

                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    transaction_hash_received,
                ),
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                trend_cancel,
            ),
        ],

        allow_reentry=True,
    )

    application.add_handler(
        trend_conversation
    )

    # ========================================================
    # START
    # ========================================================

    print(
        "The Block Room is starting..."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
