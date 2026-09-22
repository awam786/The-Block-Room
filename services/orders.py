from decimal import Decimal

from database.connection import get_pool


PAYMENT_CHAINS = {
    "bnb": {
        "chain": "BNB",
        "network": "USDT BEP-20",
    },
    "ethereum": {
        "chain": "ETH",
        "network": "USDT ERC-20",
    },
    "solana": {
        "chain": "SOL",
        "network": "USDT SPL",
    },
}


async def create_order(
    telegram_id: int,
    chain: str,
    contract_address: str,
    token_name: str,
    token_symbol: str,
    duration_hours: int,
    amount_usdt: Decimal,
    waiting_for_launch: bool,
):
    pool = get_pool()

    payment_info = PAYMENT_CHAINS.get(chain)

    if not payment_info:
        raise ValueError(
            "Payment network is not configured for this chain."
        )

    async with pool.acquire() as conn:
        async with conn.transaction():

            user_id = await conn.fetchval(
                """
                SELECT id
                FROM users
                WHERE telegram_id = $1
                """,
                telegram_id,
            )

            if not user_id:
                raise ValueError(
                    "User is not registered."
                )

            # Reserve the next order ID.
            order_id = await conn.fetchval(
                """
                SELECT nextval(
                    pg_get_serial_sequence(
                        'orders',
                        'id'
                    )
                )
                """
            )

            order_number = f"TR-{int(order_id):06d}"

            await conn.execute(
                """
                INSERT INTO orders (
                    id,
                    order_number,
                    user_id,
                    chain,
                    contract_address,
                    token_name,
                    token_symbol,
                    duration_hours,
                    amount_usdt,
                    status,
                    payment_chain,
                    payment_verified,
                    waiting_for_launch
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9,
                    'PENDING_PAYMENT',
                    $10,
                    FALSE,
                    $11
                )
                """,
                order_id,
                order_number,
                user_id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                duration_hours,
                amount_usdt,
                payment_info["chain"],
                waiting_for_launch,
            )

    return {
        "id": order_id,
        "order_number": order_number,
        "payment_chain": payment_info["chain"],
        "payment_network": payment_info["network"],
        "amount_usdt": amount_usdt,
        "waiting_for_launch": waiting_for_launch,
    }


async def get_payment_wallet(
    payment_chain: str,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT chain, address, token_symbol
            FROM wallets
            WHERE chain = $1
              AND is_active = TRUE
            """,
            payment_chain,
        )

    if not row:
        return None

    return {
        "chain": row["chain"],
        "address": row["address"],
        "token_symbol": row["token_symbol"],
    }


async def submit_transaction_hash(
    order_id: int,
    tx_hash: str,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():

            existing = await conn.fetchrow(
                """
                SELECT
                    id,
                    order_number,
                    status
                FROM orders
                WHERE payment_tx_hash = $1
                """,
                tx_hash,
            )

            if existing:
                return {
                    "success": False,
                    "reason": "TX_HASH_ALREADY_USED",
                    "order_number": existing["order_number"],
                }

            order = await conn.fetchrow(
                """
                SELECT
                    id,
                    order_number,
                    status
                FROM orders
                WHERE id = $1
                FOR UPDATE
                """,
                order_id,
            )

            if not order:
                return {
                    "success": False,
                    "reason": "ORDER_NOT_FOUND",
                }

            if order["status"] != "PENDING_PAYMENT":
                return {
                    "success": False,
                    "reason": "ORDER_NOT_ACCEPTING_PAYMENT",
                    "order_number": order["order_number"],
                }

            await conn.execute(
                """
                UPDATE orders
                SET
                    payment_tx_hash = $1,
                    status = 'PAYMENT_SUBMITTED',
                    updated_at = NOW()
                WHERE id = $2
                """,
                tx_hash,
                order_id,
            )

    return {
        "success": True,
        "order_number": order["order_number"],
    }
