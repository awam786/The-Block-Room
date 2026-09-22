import asyncio

from database.connection import get_pool
from services.payment_verifier import verify_order_payment


CHECK_INTERVAL_SECONDS = 30


async def get_pending_orders():
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id
            FROM orders
            WHERE status = 'PAYMENT_SUBMITTED'
              AND payment_tx_hash IS NOT NULL
            ORDER BY updated_at ASC
            LIMIT 50
            """
        )

    return [row["id"] for row in rows]


async def process_payment_order(
    order_id: int,
):
    try:
        result = await verify_order_payment(
            order_id
        )

        if result.get("verified"):
            print(
                f"Payment verified for order ID "
                f"{order_id}."
            )

        elif result.get("pending"):
            print(
                f"Payment still pending for order ID "
                f"{order_id}: "
                f"{result.get('reason')}"
            )

        else:
            print(
                f"Payment verification failed for order ID "
                f"{order_id}: "
                f"{result.get('reason')}"
            )

    except Exception as exc:
        print(
            f"Payment worker error for order "
            f"{order_id}: {exc}"
        )


async def payment_worker():
    print(
        "Payment verification worker started."
    )

    while True:

        try:
            order_ids = await get_pending_orders()

            if order_ids:
                for order_id in order_ids:
                    await process_payment_order(
                        order_id
                    )

        except Exception as exc:
            print(
                f"Payment worker loop error: {exc}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL_SECONDS
        )
