from decimal import Decimal
from typing import Optional

from database.connection import get_pool


PAYMENT_CHAINS = {
    "bnb": {
        "network": "BNB Smart Chain",
        "payment_asset": "USDT",
        "payment_standard": "BEP-20",
    },
    "ethereum": {
        "network": "Ethereum",
        "payment_asset": "USDT",
        "payment_standard": "ERC-20",
    },
    "solana": {
        "network": "Solana",
        "payment_asset": "USDT",
        "payment_standard": "SPL",
    },
}


async def create_order(
    user_id: int,
    chain: str,
    token_address: str,
    token_name: str,
    token_symbol: str,
    duration_hours: int,
    amount: Decimal,
):
    chain = chain.lower().strip()

    if chain not in PAYMENT_CHAINS:
        raise ValueError(
            "Unsupported payment chain."
        )

    pool = await get_pool()

    async with pool.acquire() as connection:
        wallet = await connection.fetchval(
            """
            SELECT wallet_address
            FROM payment_wallets
            WHERE LOWER(chain) = LOWER($1)
              AND enabled = TRUE
            LIMIT 1;
            """,
            chain,
        )

        if not wallet:
            raise ValueError(
                "Payment wallet is not configured "
                f"for {chain}."
            )

        order_id = await connection.fetchval(
            """
            INSERT INTO orders (
                user_id,
                chain,
                token_address,
                token_name,
                token_symbol,
                duration_hours,
                amount,
                payment_wallet,
                status,
                created_at
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
                'PAYMENT_PENDING',
                NOW()
            )
            RETURNING order_id;
            """,
            user_id,
            chain,
            token_address,
            token_name,
            token_symbol,
            duration_hours,
            amount,
            wallet,
        )

        return {
            "order_id": order_id,
            "chain": chain,
            "network": PAYMENT_CHAINS[
                chain
            ]["network"],
            "payment_asset": PAYMENT_CHAINS[
                chain
            ]["payment_asset"],
            "payment_standard": PAYMENT_CHAINS[
                chain
            ]["payment_standard"],
            "amount": amount,
            "payment_wallet": wallet,
        }


async def get_order(
    order_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT *
            FROM orders
            WHERE order_id = $1
            LIMIT 1;
            """,
            order_id,
        )


async def get_order_for_user(
    order_id: int,
    user_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT *
            FROM orders
            WHERE order_id = $1
              AND user_id = $2
            LIMIT 1;
            """,
            order_id,
            user_id,
        )


async def submit_transaction_hash(
    order_id: int,
    user_id: int,
    tx_hash: str,
):
    tx_hash = tx_hash.strip()

    if not tx_hash:
        raise ValueError(
            "Transaction hash cannot be empty."
        )

    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE orders
            SET
                transaction_hash = $1,
                status = 'PAYMENT_SUBMITTED',
                updated_at = NOW()
            WHERE order_id = $2
              AND user_id = $3
              AND status IN (
                  'PAYMENT_PENDING',
                  'PAYMENT_SUBMITTED'
              );
            """,
            tx_hash,
            order_id,
            user_id,
        )

        if result == "UPDATE 0":
            return False

        return True


async def mark_order_paid(
    order_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE orders
            SET
                status = 'PAID',
                paid_at = NOW(),
                updated_at = NOW()
            WHERE order_id = $1
              AND status = 'PAYMENT_SUBMITTED';
            """,
            order_id,
        )

        return result == "UPDATE 1"


async def mark_order_failed(
    order_id: int,
    reason: Optional[str] = None,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE orders
            SET
                status = 'PAYMENT_FAILED',
                payment_error = $1,
                updated_at = NOW()
            WHERE order_id = $2
              AND status = 'PAYMENT_SUBMITTED';
            """,
            reason,
            order_id,
        )

        return result == "UPDATE 1"


async def mark_order_waiting_for_launch(
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
            WHERE order_id = $1
              AND status = 'PAID';
            """,
            order_id,
        )

        return result == "UPDATE 1"


async def mark_order_active(
    order_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE orders
            SET
                status = 'ACTIVE',
                updated_at = NOW()
            WHERE order_id = $1
              AND status IN (
                  'PAID',
                  'PAID_WAITING_FOR_LAUNCH'
              );
            """,
            order_id,
        )

        return result == "UPDATE 1"


async def mark_order_expired(
    order_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE orders
            SET
                status = 'EXPIRED',
                updated_at = NOW()
            WHERE order_id = $1
              AND status = 'ACTIVE';
            """,
            order_id,
        )

        return result == "UPDATE 1"


async def get_payment_wallet(
    chain: str,
):
    chain = chain.lower().strip()

    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            SELECT wallet_address
            FROM payment_wallets
            WHERE LOWER(chain) = LOWER($1)
              AND enabled = TRUE
            LIMIT 1;
            """,
            chain,
        )


async def get_order_payment_wallet(
    order_id: int,
):
    """
    Returns the wallet that was stored on the
    order when the order was created.

    This is intentionally different from
    get_payment_wallet(), which returns the
    currently configured wallet.
    """

    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            SELECT payment_wallet
            FROM orders
            WHERE order_id = $1
            LIMIT 1;
            """,
            order_id,
        )
