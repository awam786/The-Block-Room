import asyncio
from datetime import datetime, timedelta, timezone

from database.connection import get_pool
from services.dex import (
    get_token_pairs,
    choose_best_pair,
    parse_pair,
)
from services.helius import get_solana_asset


CHECK_INTERVAL_SECONDS = 30


# ============================================================
# GET PAID ORDERS READY FOR ACTIVATION
# ============================================================

async def get_paid_orders():
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                o.id,
                o.order_number,
                o.chain,
                o.contract_address,
                o.token_name,
                o.token_symbol,
                o.duration_hours,
                o.amount_usdt,
                o.status,
                o.waiting_for_launch,
                o.starts_at,
                o.expires_at
            FROM orders o
            LEFT JOIN trends t
                ON t.order_id = o.id
            WHERE o.status = 'PAID'
              AND t.id IS NULL
            ORDER BY o.updated_at ASC
            LIMIT 50
            """
        )

    return rows


# ============================================================
# GET WAITING-FOR-LAUNCH ORDERS
# ============================================================

async def get_waiting_orders():
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                o.id,
                o.order_number,
                o.chain,
                o.contract_address,
                o.token_name,
                o.token_symbol,
                o.duration_hours,
                o.amount_usdt,
                o.status,
                o.waiting_for_launch,
                o.starts_at,
                o.expires_at
            FROM orders o
            WHERE o.status = 'WAITING_FOR_LAUNCH'
            ORDER BY o.updated_at ASC
            LIMIT 50
            """
        )

    return rows


# ============================================================
# CHECK TOKEN PAIR
# ============================================================

async def find_live_pair(
    chain: str,
    contract_address: str,
):
    if chain not in {
        "bnb",
        "ethereum",
        "solana",
    }:
        return None

    try:
        pairs = await get_token_pairs(
            chain,
            contract_address,
        )

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
            f"DEX pair check failed for "
            f"{chain} {contract_address}: {exc}"
        )

        return None


# ============================================================
# UPDATE ORDER TOKEN INFORMATION
# ============================================================

async def update_order_token_data(
    order_id: int,
    market_data: dict,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE orders
            SET
                token_name = COALESCE($1, token_name),
                token_symbol = COALESCE($2, token_symbol),
                updated_at = NOW()
            WHERE id = $3
            """,
            market_data.get("name"),
            market_data.get("symbol"),
            order_id,
        )


# ============================================================
# ACTIVATE TREND
# ============================================================

async def activate_order(
    order,
    market_data: dict,
):
    pool = get_pool()

    now = datetime.now(
        timezone.utc
    )

    expires_at = (
        now
        + timedelta(
            hours=order["duration_hours"]
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
        or "Unknown"
    )

    async with pool.acquire() as conn:
        async with conn.transaction():

            # Lock the order so two workers cannot
            # activate it simultaneously.
            current_order = await conn.fetchrow(
                """
                SELECT
                    id,
                    status,
                    waiting_for_launch
                FROM orders
                WHERE id = $1
                FOR UPDATE
                """,
                order["id"],
            )

            if not current_order:
                return False

            if current_order["status"] not in {
                "PAID",
                "WAITING_FOR_LAUNCH",
            }:
                return False

            existing_trend = await conn.fetchval(
                """
                SELECT id
                FROM trends
                WHERE order_id = $1
                """,
                order["id"],
            )

            if existing_trend:
                return False

            await conn.execute(
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
                )
                """,
                order["id"],
                order["chain"],
                order["contract_address"],
                token_name,
                token_symbol,
                market_data.get("volume_24h") or 0,
                market_data.get("market_cap") or 0,
                market_data.get("liquidity_usd") or 0,
                market_data.get("price_change_24h") or 0,
                now,
                expires_at,
            )

            await conn.execute(
                """
                UPDATE orders
                SET
                    status = 'ACTIVE',
                    waiting_for_launch = FALSE,
                    starts_at = $1,
                    expires_at = $2,
                    token_name = $3,
                    token_symbol = $4,
                    updated_at = NOW()
                WHERE id = $5
                """,
                now,
                expires_at,
                token_name,
                token_symbol,
                order["id"],
            )

    print(
        f"Order {order['order_number']} activated."
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
        order["contract_address"],
    )

    if not market_data:
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
        order["contract_address"],
    )

    if not market_data:
        return

    await activate_order(
        order,
        market_data,
    )


# ============================================================
# EXPIRE ACTIVE TRENDS
# ============================================================

async def expire_finished_trends():
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE trends
            SET
                status = 'EXPIRED',
                updated_at = NOW()
            WHERE status = 'ACTIVE'
              AND expires_at IS NOT NULL
              AND expires_at <= NOW()
            RETURNING order_id
            """
        )

        for row in rows:
            await conn.execute(
                """
                UPDATE orders
                SET
                    status = 'EXPIRED',
                    updated_at = NOW()
                WHERE id = $1
                  AND status = 'ACTIVE'
                """,
                row["order_id"],
            )

    if rows:
        print(
            f"Expired {len(rows)} trend(s)."
        )


# ============================================================
# MOVE PAID PRE-LAUNCH ORDERS
# ============================================================

async def normalize_paid_orders():
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id
            FROM orders
            WHERE status = 'PAID'
              AND waiting_for_launch = TRUE
            """
        )

        for row in rows:
            await conn.execute(
                """
                UPDATE orders
                SET
                    status = 'WAITING_FOR_LAUNCH',
                    updated_at = NOW()
                WHERE id = $1
                  AND status = 'PAID'
                """,
                row["id"],
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
            await normalize_paid_orders()

            paid_orders = await get_paid_orders()

            for order in paid_orders:
                await process_paid_order(
                    order
                )

            waiting_orders = await get_waiting_orders()

            for order in waiting_orders:
                await process_waiting_order(
                    order
                )

            await expire_finished_trends()

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                f"Trend activation worker error: {exc}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL_SECONDS
        )
