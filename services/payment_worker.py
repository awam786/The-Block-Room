import asyncio

from telegram import Bot

from config import BOT_TOKEN

from database.connection import get_pool

from services.payment_verifier import verify_payment

from services.orders import (
    mark_order_paid,
    mark_order_failed,
)


POLL_SECONDS = 30


async def get_submitted_orders():
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                order_id,
                user_id,
                chain,
                amount,
                transaction_hash,
                status
            FROM orders
            WHERE status = 'PAYMENT_SUBMITTED'
              AND transaction_hash IS NOT NULL
              AND transaction_hash <> ''
            ORDER BY created_at ASC
            LIMIT 100;
            """
        )

        return rows


async def send_payment_message(
    bot: Bot,
    user_id: int,
    text: str,
):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            disable_web_page_preview=True,
        )

    except Exception as exc:
        print(
            "Payment notification error "
            f"user={user_id}: {exc}"
        )


async def process_order(
    bot: Bot,
    order,
):
    order_id = order["order_id"]
    user_id = order["user_id"]
    tx_hash = order["transaction_hash"]

    if not tx_hash:
        return

    result = await verify_payment(
        order_id,
        tx_hash,
    )

    # ---------------------------------------------------------
    # PAYMENT VERIFIED
    # ---------------------------------------------------------

    if result.get("valid"):
        changed = await mark_order_paid(
            order_id
        )

        if not changed:
            return

        amount = result.get("amount")

        if amount is None:
            amount = order["amount"]

        await send_payment_message(
            bot,
            user_id,
            (
                "✅ Payment confirmed!\n\n"
                f"Order: #{order_id}\n"
                f"Received: {amount} USDT\n\n"
                "Your order has been marked as PAID."
            ),
        )

        return

    # ---------------------------------------------------------
    # TEMPORARY / RPC / API ISSUE
    # ---------------------------------------------------------

    if result.get("pending"):
        print(
            "Payment verification pending "
            f"order={order_id}: "
            f"{result.get('reason')}"
        )

        return

    # ---------------------------------------------------------
    # INVALID PAYMENT
    # ---------------------------------------------------------

    reason = (
        result.get("reason")
        or "Payment could not be verified."
    )

    changed = await mark_order_failed(
        order_id,
        reason,
    )

    if not changed:
        return

    await send_payment_message(
        bot,
        user_id,
        (
            "❌ Payment verification failed.\n\n"
            f"Order: #{order_id}\n\n"
            f"Reason: {reason}\n\n"
            "Please contact support if you believe "
            "this payment is correct."
        ),
    )


async def payment_worker():
    print(
        "Payment worker started."
    )

    bot = Bot(
        token=BOT_TOKEN
    )

    try:
        while True:
            try:
                orders = await get_submitted_orders()

                for order in orders:
                    try:
                        await process_order(
                            bot,
                            order,
                        )

                    except Exception as exc:
                        print(
                            "Payment order processing "
                            f"error order="
                            f"{order['order_id']}: "
                            f"{exc}"
                        )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                print(
                    "Payment worker error: "
                    f"{exc}"
                )

            await asyncio.sleep(
                POLL_SECONDS
            )

    finally:
        try:
            await bot.shutdown()
        except Exception as exc:
            print(
                "Payment bot shutdown error: "
                f"{exc}"
            )
