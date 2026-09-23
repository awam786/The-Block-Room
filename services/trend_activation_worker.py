import asyncio
from typing import Any, Dict, List, Optional

from services.dex import get_best_token_pair
from services.orders import (
    get_order,
    mark_order_active,
    mark_order_expired,
    mark_order_waiting_for_launch,
)
from database.connection import get_pool


POLL_SECONDS = 30


SUPPORTED_CHAINS = {
    "bnb",
    "ethereum",
    "solana",
    "robinhood",
}


def normalize_chain(
    chain: str,
) -> str:
    value = (
        str(chain)
        .strip()
        .lower()
    )

    aliases = {
        "bsc": "bnb",
        "binance": "bnb",
        "binance-smart-chain": "bnb",
        "eth": "ethereum",
        "sol": "solana",
        "rh": "robinhood",
        "robinhood-chain": "robinhood",
    }

    return aliases.get(
        value,
        value,
    )


async def get_paid_orders() -> List[Dict[str, Any]]:
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
                o.status,
                o.created_at,
                o.paid_at,
                o.activated_at,
                o.expires_at,
                t.id AS trend_id,
                t.status AS trend_status,
                t.activated_at AS trend_activated_at,
                t.expires_at AS trend_expires_at
            FROM orders o
            LEFT JOIN trends t
                ON t.order_id = o.id
            WHERE o.status IN (
                'PAID',
                'PAID_WAITING_FOR_LAUNCH',
                'ACTIVE'
            )
            ORDER BY o.created_at ASC;
            """
        )

        return [
            dict(row)
            for row in rows
        ]


async def get_waiting_orders() -> List[Dict[str, Any]]:
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                id,
                order_id,
                user_id,
                chain,
                token_address,
                token_name,
                token_symbol,
                duration_hours,
                amount,
                status,
                created_at,
                paid_at,
                activated_at,
                expires_at
            FROM orders
            WHERE status = 'PAID_WAITING_FOR_LAUNCH'
            ORDER BY created_at ASC
            LIMIT 200;
            """
        )

        return [
            dict(row)
            for row in rows
        ]


async def find_live_pair(
    chain: str,
    token_address: str,
) -> Optional[Dict[str, Any]]:
    chain = normalize_chain(
        chain
    )

    if chain not in SUPPORTED_CHAINS:
        return None

    if not token_address:
        return None

    try:
        return await get_best_token_pair(
            chain,
            token_address,
        )

    except Exception as exc:
        print(
            "Live pair lookup error "
            f"chain={chain} "
            f"token={token_address}: "
            f"{exc}"
        )
        return None


async def activate_order(
    order: Dict[str, Any],
    pair: Dict[str, Any],
):
    order_id = order["order_id"]

    token_name = (
        pair.get("name")
        or order.get("token_name")
        or "Unknown"
    )

    token_symbol = (
        pair.get("symbol")
        or order.get("token_symbol")
        or "UNKNOWN"
    )

    try:
        changed = await mark_order_active(
            order_id=order_id,
            token_name=token_name,
            token_symbol=token_symbol,
        )

    except TypeError:
        # Compatibility with the existing orders
        # service if it only accepts order_id.
        changed = await mark_order_active(
            order_id
        )

    if not changed:
        print(
            "Order was not activated "
            f"order={order_id}"
        )
        return False

    print(
        "Trend activated "
        f"order={order_id} "
        f"token={token_symbol}"
    )

    return True


async def refresh_waiting_order(
    order: Dict[str, Any],
):
    order_id = order["order_id"]

    chain = normalize_chain(
        order["chain"]
    )

    token_address = (
        order["token_address"]
    )

    if chain not in SUPPORTED_CHAINS:
        print(
            "Unsupported waiting-order chain "
            f"order={order_id} "
            f"chain={chain}"
        )
        return

    pair = await find_live_pair(
        chain,
        token_address,
    )

    if not pair:
        return

    print(
        "Live pair detected "
        f"order={order_id} "
        f"chain={chain} "
        f"token={token_address}"
    )

    await activate_order(
        order,
        pair,
    )


async def ensure_paid_order_state(
    order: Dict[str, Any],
):
    """
    Makes sure a paid order that does not have
    a live market pair remains in the correct
    pre-launch state.
    """

    order_id = order["order_id"]

    if order["status"] != "PAID":
        return

    chain = normalize_chain(
        order["chain"]
    )

    pair = await find_live_pair(
        chain,
        order["token_address"],
    )

    if pair:
        await activate_order(
            order,
            pair,
        )
        return

    try:
        await mark_order_waiting_for_launch(
            order_id
        )

    except Exception as exc:
        print(
            "Could not move order to "
            "waiting-for-launch "
            f"order={order_id}: {exc}"
        )


async def expire_old_active_orders():
    """
    The orders service is responsible for calculating
    and applying expiry. This worker only asks it to
    process orders whose expiry timestamp has passed.
    """

    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                id,
                order_id,
                status
            FROM orders
            WHERE status = 'ACTIVE'
              AND expires_at IS NOT NULL
              AND expires_at <= NOW()
            ORDER BY expires_at ASC
            LIMIT 200;
            """
        )

    for row in rows:
        order_id = row["order_id"]

        try:
            changed = await mark_order_expired(
                order_id
            )

            if changed:
                print(
                    "Order expired "
                    f"order={order_id}"
                )

        except Exception as exc:
            print(
                "Order expiry error "
                f"order={order_id}: {exc}"
            )


async def process_waiting_orders():
    orders = await get_waiting_orders()

    if not orders:
        return

    for order in orders:
        try:
            await refresh_waiting_order(
                order
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                "Waiting order processing error "
                f"order={order['order_id']}: "
                f"{exc}"
            )


async def process_paid_orders():
    orders = await get_paid_orders()

    if not orders:
        return

    for order in orders:
        try:
            if order["status"] == "PAID":
                await ensure_paid_order_state(
                    order
                )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                "Paid order processing error "
                f"order={order['order_id']}: "
                f"{exc}"
            )


async def process_expired_orders():
    try:
        await expire_old_active_orders()

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        print(
            "Expired order processing error: "
            f"{exc}"
        )


async def trend_activation_worker():
    print(
        "Trend activation worker started."
    )

    while True:
        try:
            await process_paid_orders()

            await process_waiting_orders()

            await process_expired_orders()

        except asyncio.CancelledError:
            print(
                "Trend activation worker "
                "stopping..."
            )
            raise

        except Exception as exc:
            print(
                "Trend activation worker error: "
                f"{exc}"
            )

        await asyncio.sleep(
            POLL_SECONDS
        )
