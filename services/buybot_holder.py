from database.connection import get_pool


async def has_previous_buy(
    chain: str,
    token_address: str,
    buyer_address: str,
) -> bool:
    if not chain:
        return False

    if not token_address:
        return False

    if not buyer_address:
        return False

    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT id
            FROM buybot_events
            WHERE LOWER(chain) = LOWER($1)
              AND LOWER(token_address) = LOWER($2)
              AND LOWER(buyer_address) =
                  LOWER($3)
            LIMIT 1;
            """,
            chain,
            token_address,
            buyer_address,
        )

        return row is not None


async def is_first_observed_holder(
    chain: str,
    token_address: str,
    buyer_address: str,
) -> bool:
    return not await has_previous_buy(
        chain,
        token_address,
        buyer_address,
    )
