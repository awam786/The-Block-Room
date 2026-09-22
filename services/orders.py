from decimal import Decimal
from typing import Optional

from database.connection import get_pool


PAYMENT_CHAINS = {
    "bnb": "bnb",
    "ethereum": "ethereum",
    "solana": "solana",
}


async def create_order(
    user_id: int,
    chain: str,
    token_address: str,
    token_name: Optional[str],
    token_symbol: Optional[str],
    duration_hours: int,
    amount: Decimal,
):
    chain = (
        chain
        or ""
    ).lower().strip()

    if chain not in PAYMENT_CHAINS:
        raise ValueError(
            "Unsupported payment chain."
        )

    pool = await get_pool()

    async with pool.acquire() as connection:
        async with connection.transaction():

            # -----------------------------------------------------
            # LOCK THE CURRENT PAYMENT WALLET INTO THIS ORDER
            # -----------------------------------------------------

            payment_wallet = await connection.fetchval(
                """
                SELECT wallet_address
                FROM payment_wallets
                WHERE LOWER(chain) = LOWER($1)
                  AND enabled = TRUE
                LIMIT 1;
                """,
                chain,
            )

            if not payment_wallet:
                raise ValueError(
                    f"No payment wallet is configured "
                    f"for {chain}."
                )

            # -----------------------------------------------------
            # CREATE DATABASE ORDER
            # -----------------------------------------------------

            database_id = await connection.fetchval(
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
                    created_at,
                    updated_at
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
                    NOW(),
                    NOW()
                )
                RETURNING id;
                """,
                user_id,
                chain,
                token_address,
                token_name,
                token_symbol,
                duration_hours,
                amount,
                payment_wallet,
            )

            if not database_id:
                raise RuntimeError(
                    "Failed to create order."
                )

            # -----------------------------------------------------
            # GENERATE PUBLIC ORDER ID
            # Example: TR-000001
            # -----------------------------------------------------

            order_id = (
                f"TR-{int(database_id):06d}"
            )

            await connection.execute(
                """
                UPDATE orders
                SET
                    order_id = $1,
                    updated_at = NOW()
                WHERE id = $2;
                """,
                order_id,
                database_id,
            )

            return {
                "id": database_id,
                "order_id": order_id,
                "user_id": user_id,
                "chain": chain,
                "token_address": token_address,
                "token_name": token_name,
                "token_symbol": token_symbol,
                "duration_hours": duration_hours,
                "amount": amount,
                "payment_wallet": payment_wallet,
                "status": "PAYMENT_PENDING",
            }


async def get_order(
    order_id: str,
):
    if not order_id:
        return None

    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
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
                payment_wallet,
                transaction_hash,
                status,
                payment_error,
                created_at,
                updated_at,
                paid_at,
                activated_at,
                expires_at
            FROM orders
            WHERE order_id = $1
            LIMIT 1;
            """,
            order_id,
        )


async def get_order_for_user(
    order_id: str,
    user_id: int,
):
    if not order_id:
        return None

    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
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
                payment_wallet,
                transaction_hash,
                status,
                payment_error,
                created_at,
                updated_at,
                paid_at,
                activated_at,
                expires_at
            FROM orders
            WHERE order_id = $1
              AND user_id = $2
            LIMIT 1;
            """,
            order_id,
            user_id,
        )


async def submit_transaction_hash(
    order_id: str,
    tx_hash: str,
):
    if not order_id:
        return False

    if not tx_hash:
        return False

    tx_hash = tx_hash.strip()

    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE orders
            SET
                transaction_hash = $1,
                status = 'PAYMENT_SUBMITTED',
                payment_error = NULL,
                updated_at = NOW()
            WHERE order_id = $2
              AND status IN (
                  'PAYMENT_PENDING',
                  'PAYMENT_SUBMITTED'
              );
            """,
            tx_hash,
            order_id,
        )

        return result == "UPDATE 1"


async def mark_order_paid(
    order_id: str,
):
    if not order_id:
        return False

    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE orders
            SET
                status = 'PAID',
                paid_at = COALESCE(
                    paid_at,
                    NOW()
                ),
                payment_error = NULL,
                updated_at = NOW()
            WHERE order_id = $1
              AND status IN (
                  'PAYMENT_PENDING',
                  'PAYMENT_SUBMITTED'
              );
            """,
            order_id,
        )

        return result == "UPDATE 1"


async def mark_order_failed(
    order_id: str,
    reason: str,
):
    if not order_id:
        return False

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
              AND status IN (
                  'PAYMENT_PENDING',
                  'PAYMENT_SUBMITTED'
              );
            """,
            reason,
            order_id,
        )

        return result == "UPDATE 1"


async def mark_order_waiting_for_launch(
    order_id: str,
):
    if not order_id:
        return False

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
    order_id: str,
    expires_at=None,
):
    if not order_id:
        return False

    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE orders
            SET
                status = 'ACTIVE',
                activated_at = COALESCE(
                    activated_at,
                    NOW()
                ),
                expires_at = COALESCE(
                    $2,
                    expires_at
                ),
                updated_at = NOW()
            WHERE order_id = $1
              AND status IN (
                  'PAID',
                  'PAID_WAITING_FOR_LAUNCH'
              );
            """,
            order_id,
            expires_at,
        )

        return result == "UPDATE 1"


async def mark_order_expired(
    order_id: str,
):
    if not order_id:
        return False

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
    chain = (
        chain
        or ""
    ).lower().strip()

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
