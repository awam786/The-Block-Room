import asyncio

from telegram import Bot

from config import BOT_TOKEN
from database.connection import get_pool
from services.payment_verifier import verify_order_payment


CHECK_INTERVAL_SECONDS = 30


async def get_pending_orders():
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                o.id,
                o.order_number,
                o.user_id,
                u.telegram_id
            FROM orders o
            JOIN users u
                ON u.id = o.user_id
            WHERE o.status = 'PAYMENT_SUBMITTED'
              AND o.payment_tx_hash IS NOT NULL
            ORDER BY o.updated_at ASC
            LIMIT 50
            """
        )

    return rows


async def mark_payment_notification_sent(
    order_id: int,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE orders
            SET
                updated_at = NOW()
            WHERE id = $1
            """,
            order_id,
        )

    return result == "UPDATE 1"


async def send_payment_verified_message(
    bot: Bot,
    order_number: str,
    telegram_id: int,
):
    await bot.send_message(
        chat_id=telegram_id,
        text=(
            "✅ *PAYMENT VERIFIED*\n\n"
            f"🧾 Order: `{order_number}`\n\n"
            "💰 Your USDT payment has been successfully "
            "verified on-chain.\n\n"
            "🚀 Your trending order is now being activated."
        ),
        parse_mode="Markdown",
    )


async def send_payment_pending_message(
    bot: Bot,
    order_number: str,
    telegram_id: int,
    reason: str,
):
    await bot.send_message(
        chat_id=telegram_id,
        text=(
            "⏳ *PAYMENT VERIFICATION IN PROGRESS*\n\n"
            f"🧾 Order: `{order_number}`\n\n"
            f"{reason}\n\n"
            "No action is required right now. "
            "The system will continue checking your payment."
        ),
        parse_mode="Markdown",
    )


async def send_payment_failed_message(
    bot: Bot,
    order_number: str,
    telegram_id: int,
    reason: str,
):
    await bot.send_message(
        chat_id=telegram_id,
        text=(
            "❌ *PAYMENT VERIFICATION FAILED*\n\n"
            f"🧾 Order: `{order_number}`\n\n"
            f"Reason:\n{reason}\n\n"
            "Please check your transaction details. "
            "If you believe this is incorrect, contact support."
        ),
        parse_mode="Markdown",
    )


async def process_payment_order(
    bot: Bot,
    order,
):
    order_id = order["id"]
    order_number = order["order_number"]
    telegram_id = order["telegram_id"]

    try:
        result = await verify_order_payment(
            order_id
        )

        if result.get("verified"):

            print(
                f"Payment verified for order "
                f"{order_number}."
            )

            try:
                await send_payment_verified_message(
                    bot,
                    order_number,
                    telegram_id,
                )

            except Exception as exc:
                print(
                    f"Could not notify user for "
                    f"order {order_number}: {exc}"
                )

            return

        if result.get("pending"):

            print(
                f"Payment still pending for order "
                f"{order_number}: "
                f"{result.get('reason')}"
            )

            return

        print(
            f"Payment verification failed for order "
            f"{order_number}: "
            f"{result.get('reason')}"
        )

        try:
            await send_payment_failed_message(
                bot,
                order_number,
                telegram_id,
                result.get(
                    "reason",
                    "Payment could not be verified.",
                ),
            )

        except Exception as exc:
            print(
                f"Could not notify user about failed "
                f"payment for order {order_number}: {exc}"
            )

    except Exception as exc:
        print(
            f"Payment worker error for order "
            f"{order_number}: {exc}"
        )


async def payment_worker():
    print(
        "Payment verification worker started."
    )

    bot = Bot(
        token=BOT_TOKEN
    )

    while True:

        try:
            orders = await get_pending_orders()

            if orders:

                for order in orders:

                    await process_payment_order(
                        bot,
                        order,
                    )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                f"Payment worker loop error: {exc}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL_SECONDS
        )
