import asyncio
from datetime import datetime, timedelta, timezone

from database.connection import get_pool

from services.dex import (
    get_token_pairs,
    choose_best_pair,
    parse_pair,
)


CHECK_INTERVAL_SECONDS = 30


# ============================================================
# GET PAID ORDERS READY FOR ACTIVATION
# ============================================================

async def get_paid_orders():
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                o.id,
                o.order_id,
                o.user_id,
                o.chain,
                o.token_address,
                o.token_name,
                o.token_symbol,
                o.duration_hours,
                o.amount,
                o.payment_wallet,
                o.status,
                o.created_at,
                o.updated_at
            FROM orders o
            LEFT JOIN trends t
                ON t.order_id = o.id
            WHERE o.status = 'PAID'
              AND t.id IS NULL
            ORDER BY o.updated_at ASC
            LIMIT 50;
            """
        )

        return rows


# ============================================================
# GET WAITING-FOR-LAUNCH ORDERS
# ============================================================

async def get_waiting_orders():
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                o.id,
                o.order_id,
                o.user_id,
                o.chain,
                o.token_address,
                o.token_name,
                o.token_symbol,
                o.duration_hours,
                o.amount,
                o.payment_wallet,
                o.status,
                o.created_at,
                o.updated_at
            FROM orders o
            WHERE o.status = 'PAID_WAITING_FOR_LAUNCH'
            ORDER BY o.updated_at ASC
            LIMIT 50;
            """
        )

        return rows


# ============================================================
# FIND LIVE TOKEN PAIR
# ============================================================

async def find_live_pair(
    chain: str,
    token_address: str,
):
    normalized_chain = (
        chain
        or ""
    ).lower().strip()

    if normalized_chain not in {
        "bnb",
        "ethereum",
        "solana",
    }:
        return None

    if not token_address:
        return None

    try:
        pairs = await get_token_pairs(
            normalized_chain,
            token_address,
        )

        if not pairs:
            return None

        pair = choose_best_pair(
            pairs
        )

        if not pair:
            return None

        return parse_pair(
            pair
        )

    except Exception as exc:
        print(
            "DEX pair check failed "
            f"for {normalized_chain} "
            f"{token_address}: {exc}"
        )

        return None


# ============================================================
# ACTIVATE TREND
# ============================================================

async def activate_order(
    order,
    market_data: dict,
):
    pool = await get_pool()

    now = datetime.now(
        timezone.utc
    )

    duration_hours = int(
        order["duration_hours"]
    )

    expires_at = (
        now
        + timedelta(
            hours=duration_hours
        )
    )

    token_name = (
        market_data.get("name")
        or order["token_name"]
        or "Unknown"
    )

    token_symbol = (
        market_data.get("symbol")
        or order["token_symbol"]
        or "UNKNOWN"
    )

    token_address = (
        market_data.get("address")
        or order["token_address"]
    )

    async with pool.acquire() as connection:
        async with connection.transaction():

            # -------------------------------------------------
            # LOCK ORDER
            # Prevent two workers from activating it twice.
            # -------------------------------------------------

            current_order = await connection.fetchrow(
                """
                SELECT
                    id,
                    order_id,
                    status,
                    chain,
                    token_address,
                    token_name,
                    token_symbol,
                    duration_hours
                FROM orders
                WHERE id = $1
                FOR UPDATE;
                """,
                order["id"],
            )

            if not current_order:
                return False

            if current_order["status"] not in {
                "PAID",
                "PAID_WAITING_FOR_LAUNCH",
            }:
                return False

            # -------------------------------------------------
            # CHECK WHETHER A TREND ALREADY EXISTS
            # -------------------------------------------------

            existing_trend = await connection.fetchval(
                """
                SELECT id
                FROM trends
                WHERE order_id = $1
                LIMIT 1;
                """,
                order["id"],
            )

            if existing_trend:
                return False

            # -------------------------------------------------
            # CREATE ACTIVE TREND
            # -------------------------------------------------

            await connection.execute(
                """
                INSERT INTO trends (
                    order_id,
                    chain,
                    contract_address,
                    token_name,
                    token_symbol,
                    status,
                    score,
                    volume_24h,
                    market_cap,
                    liquidity,
                    price_change_24h,
                    started_at,
                    expires_at,
                    updated_at
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    'ACTIVE',
                    0,
                    $6,
                    $7,
                    $8,
                    $9,
                    $10,
                    $11,
                    NOW()
                );
                """,
                order["id"],
                order["chain"],
                token_address,
                token_name,
                token_symbol,
                market_data.get(
                    "volume_24h"
                ) or 0,
                market_data.get(
                    "market_cap"
                ) or 0,
                market_data.get(
                    "liquidity_usd"
                ) or 0,
                market_data.get(
                    "price_change_24h"
                ) or 0,
                now,
                expires_at,
            )

            # -------------------------------------------------
            # MARK ORDER ACTIVE
            # -------------------------------------------------

            await connection.execute(
                """
                UPDATE orders
                SET
                    status = 'ACTIVE',
                    activated_at = COALESCE(
                        activated_at,
                        $1
                    ),
                    expires_at = $2,
                    token_name = COALESCE(
                        $3,
                        token_name
                    ),
                    token_symbol = COALESCE(
                        $4,
                        token_symbol
                    ),
                    updated_at = NOW()
                WHERE id = $5
                  AND status IN (
                      'PAID',
                      'PAID_WAITING_FOR_LAUNCH'
                  );
                """,
                now,
                expires_at,
                token_name,
                token_symbol,
                order["id"],
            )

    print(
        "Order "
        f"{order['order_id']} activated."
    )

    return True


# ============================================================
# PROCESS PAID ORDER
# ============================================================

async def process_paid_order(
    order,
):
    market_data = await find_live_pair(
        order["chain"],
        order["token_address"],
    )

    # ---------------------------------------------------------
    # NO LIVE PAIR YET
    #
    # Keep the order paid until the token launches.
    # ---------------------------------------------------------

    if not market_data:
        await move_to_waiting_for_launch(
            order["id"]
        )

        return

    await activate_order(
        order,
        market_data,
    )


# ============================================================
# PROCESS PRE-LAUNCH ORDER
# ============================================================

async def process_waiting_order(
    order,
):
    market_data = await find_live_pair(
        order["chain"],
        order["token_address"],
    )

    if not market_data:
        return

    await activate_order(
        order,
        market_data,
    )


# ============================================================
# MOVE PAID ORDER TO WAITING FOR LAUNCH
# ============================================================

async def move_to_waiting_for_launch(
    order_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE orders
            SET
                status = 'PAID_WAITING_FOR_LAUNCH',
                updated_at = NOW()
            WHERE id = $1
              AND status = 'PAID';
            """,
            order_id,
        )

        return result == "UPDATE 1"


# ============================================================
# EXPIRE ACTIVE TRENDS
# ============================================================

async def expire_finished_trends():
    pool = await get_pool()

    async with pool.acquire() as connection:
        async with connection.transaction():

            rows = await connection.fetch(
                """
                UPDATE trends
                SET
                    status = 'EXPIRED',
                    updated_at = NOW()
                WHERE status = 'ACTIVE'
                  AND expires_at IS NOT NULL
                  AND expires_at <= NOW()
                RETURNING order_id;
                """
            )

            for row in rows:
                await connection.execute(
                    """
                    UPDATE orders
                    SET
                        status = 'EXPIRED',
                        updated_at = NOW()
                    WHERE id = $1
                      AND status = 'ACTIVE';
                    """,
                    row["order_id"],
                )

    if rows:
        print(
            f"Expired {len(rows)} trend(s)."
        )


# ============================================================
# MAIN ACTIVATION WORKER
# ============================================================

async def trend_activation_worker():
    print(
        "Trend activation worker started."
    )

    while True:
        try:
            # -------------------------------------------------
            # CHECK PAID ORDERS
            # -------------------------------------------------

            paid_orders = await get_paid_orders()

            for order in paid_orders:
                try:
                    await process_paid_order(
                        order
                    )

                except Exception as exc:
                    print(
                        "Paid order processing "
                        f"error order="
                        f"{order['order_id']}: "
                        f"{exc}"
                    )

            # -------------------------------------------------
            # CHECK PRE-LAUNCH ORDERS
            # -------------------------------------------------

            waiting_orders = (
                await get_waiting_orders()
            )

            for order in waiting_orders:
                try:
                    await process_waiting_order(
                        order
                    )

                except Exception as exc:
                    print(
                        "Waiting order processing "
                        f"error order="
                        f"{order['order_id']}: "
                        f"{exc}"
                    )

            # -------------------------------------------------
            # EXPIRE FINISHED TRENDS
            # -------------------------------------------------

            await expire_finished_trends()

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                "Trend activation worker error: "
                f"{exc}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL_SECONDS
        )
