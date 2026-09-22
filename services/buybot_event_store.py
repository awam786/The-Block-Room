from typing import Optional

from database.connection import get_pool


async def ensure_buybot_event_table():
    """
    Create the BuyBot event table if it does not already exist.
    """

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS buybot_events (
                id BIGSERIAL PRIMARY KEY,

                group_id BIGINT NOT NULL,

                chain TEXT NOT NULL,

                tx_hash TEXT NOT NULL,

                token_address TEXT NOT NULL,

                token_symbol TEXT,

                buyer_address TEXT,

                spent_amount_usd NUMERIC(30, 10),

                received_amount NUMERIC(40, 10),

                created_at TIMESTAMPTZ DEFAULT NOW(),

                UNIQUE (
                    group_id,
                    chain,
                    tx_hash,
                    token_address
                )
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_buybot_events_group_created
            ON buybot_events (
                group_id,
                created_at DESC
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_buybot_events_token
            ON buybot_events (
                chain,
                token_address
            );
            """
        )


async def event_already_processed(
    group_id: int,
    chain: str,
    tx_hash: str,
    token_address: str,
) -> bool:
    """
    Check whether this transaction has already
    generated a BuyBot event for this group.
    """

    pool = await get_pool()

    async with pool.acquire() as connection:

        row = await connection.fetchrow(
            """
            SELECT id
            FROM buybot_events
            WHERE group_id = $1
              AND chain = $2
              AND tx_hash = $3
              AND token_address = $4
            LIMIT 1;
            """,
            group_id,
            chain,
            tx_hash,
            token_address,
        )

        return row is not None


async def save_buybot_event(
    group_id: int,
    chain: str,
    tx_hash: str,
    token_address: str,
    token_symbol: Optional[str] = None,
    buyer_address: Optional[str] = None,
    spent_amount_usd=None,
    received_amount=None,
) -> bool:
    """
    Save a processed BuyBot event.

    Returns:
        True  = newly saved
        False = duplicate event
    """

    pool = await get_pool()

    async with pool.acquire() as connection:

        result = await connection.execute(
            """
            INSERT INTO buybot_events (
                group_id,
                chain,
                tx_hash,
                token_address,
                token_symbol,
                buyer_address,
                spent_amount_usd,
                received_amount
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8
            )
            ON CONFLICT (
                group_id,
                chain,
                tx_hash,
                token_address
            )
            DO NOTHING;
            """,
            group_id,
            chain,
            tx_hash,
            token_address,
            token_symbol,
            buyer_address,
            spent_amount_usd,
            received_amount,
        )

        return result == "INSERT 0 1"


async def get_recent_buybot_events(
    group_id: int,
    limit: int = 20,
):
    """
    Return recent BuyBot events for a group.
    """

    pool = await get_pool()

    async with pool.acquire() as connection:

        rows = await connection.fetch(
            """
            SELECT
                id,
                group_id,
                chain,
                tx_hash,
                token_address,
                token_symbol,
                buyer_address,
                spent_amount_usd,
                received_amount,
                created_at
            FROM buybot_events
            WHERE group_id = $1
            ORDER BY created_at DESC
            LIMIT $2;
            """,
            group_id,
            limit,
        )

        return rows


async def get_token_event_count(
    chain: str,
    token_address: str,
) -> int:
    """
    Return the number of BuyBot events detected
    for a token.
    """

    pool = await get_pool()

    async with pool.acquire() as connection:

        count = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_events
            WHERE chain = $1
              AND token_address = $2;
            """,
            chain,
            token_address,
        )

        return int(count or 0)
