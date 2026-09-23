import asyncio
from decimal import Decimal

import httpx

from config import (
    HELIUS_API_KEY,
    BUYBOT_POLL_SECONDS,
)

from database.connection import get_pool


# ============================================================
# HELIUS CONFIGURATION
# ============================================================

HELIUS_BASE_URL = (
    "https://api.helius.xyz"
)

HELIUS_TRANSACTION_URL = (
    f"{HELIUS_BASE_URL}/v0/transactions"
)


# ============================================================
# DATABASE — MONITORED SOLANA TOKENS
# ============================================================

async def get_solana_tokens():

    pool = await get_pool()

    async with pool.acquire() as connection:

        rows = await connection.fetch(
            """
            SELECT
                id,
                group_id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                pair_address,
                dex_url
            FROM buybot_tokens
            WHERE enabled = TRUE
              AND chain = 'SOL'
            ORDER BY id ASC
            """
        )

    return rows


# ============================================================
# DATABASE — DUPLICATE EVENT CHECK
# ============================================================

async def event_exists(
    group_id: int,
    chain: str,
    tx_hash: str,
    token_address: str,
) -> bool:

    pool = await get_pool()

    async with pool.acquire() as connection:

        row = await connection.fetchrow(
            """
            SELECT 1
            FROM buybot_events
            WHERE group_id = $1
              AND chain = $2
              AND tx_hash = $3
              AND LOWER(token_address)
                    = LOWER($4)
            LIMIT 1
            """,
            group_id,
            chain,
            tx_hash,
            token_address,
        )

    return row is not None


# ============================================================
# DATABASE — SAVE EVENT
# ============================================================

async def save_event(
    group_id: int,
    chain: str,
    tx_hash: str,
    token_address: str,
    token_symbol: str | None,
    buyer_address: str | None,
    spent_amount_usd: Decimal,
    received_amount: Decimal,
):

    pool = await get_pool()

    async with pool.acquire() as connection:

        await connection.execute(
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
            ON CONFLICT DO NOTHING
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


# ============================================================
# DATABASE — BUYBOT SETTINGS
# ============================================================

async def get_buybot_settings(
    group_id: int,
):

    pool = await get_pool()

    async with pool.acquire() as connection:

        return await connection.fetchrow(
            """
            SELECT
                enabled,
                min_buy_usd,
                media_type,
                media_id,
                alert_title,
                alert_template,
                buy_emoji,
                new_holder_emoji,
                market_cap_emoji,
                spent_emoji,
                received_emoji,
                network_emoji
            FROM buybot_settings
            WHERE group_id = $1
            """,
            group_id,
        )


# ============================================================
# HELIUS — GET TOKEN TRANSACTIONS
# ============================================================

async def get_token_transactions(
    token_address: str,
):

    if not HELIUS_API_KEY:
        print(
            "HELIUS_API_KEY is not configured."
        )

        return []

    url = (
        f"{HELIUS_BASE_URL}/v0/addresses/"
        f"{token_address}/transactions"
    )

    params = {
        "api-key": HELIUS_API_KEY,
        "limit": 100,
    }

    try:

        timeout = httpx.Timeout(
            30.0,
            connect=10.0,
        )

        async with httpx.AsyncClient(
            timeout=timeout,
        ) as client:

            response = await client.get(
                url,
                params=params,
            )

            response.raise_for_status()

            data = response.json()

        if not isinstance(
            data,
            list,
        ):
            return []

        return data

    except Exception as exc:

        print(
            "Helius transaction request "
            f"failed for {token_address}: "
            f"{exc}"
        )

        return []


# ============================================================
# HELIUS — GET TRANSACTION DETAILS
# ============================================================

async def get_transaction(
    signature: str,
):

    if not HELIUS_API_KEY:
        return None

    params = {
        "api-key": HELIUS_API_KEY,
    }

    payload = {
        "transactions": [
            signature
        ],
    }

    try:

        timeout = httpx.Timeout(
            30.0,
            connect=10.0,
        )

        async with httpx.AsyncClient(
            timeout=timeout,
        ) as client:

            response = await client.post(
                HELIUS_TRANSACTION_URL,
                params=params,
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

        if not isinstance(
            data,
            list,
        ):
            return None

        if not data:
            return None

        return data[0]

    except Exception as exc:

        print(
            "Helius parsed transaction "
            f"request failed: {exc}"
        )

        return None


# ============================================================
# HELIUS — DETERMINE BUY
# ============================================================

def extract_buy(
    transaction: dict,
    token_address: str,
) -> dict | None:

    token_address = (
        token_address.lower()
    )

    events = transaction.get(
        "events",
        {},
    )

    token_swap = events.get(
        "swap",
        {}
    )

    token_inputs = (
        token_swap.get(
            "tokenInputs",
            []
        )
        or []
    )

    token_outputs = (
        token_swap.get(
            "tokenOutputs",
            []
        )
        or []
    )

    #
    # A BUY means the monitored token appears
    # in tokenOutputs.
    #

    received = None

    for output in token_outputs:

        mint = (
            output.get(
                "mint"
            )
            or output.get(
                "tokenAddress"
            )
        )

        if not mint:
            continue

        if mint.lower() != token_address:
            continue

        amount = (
            output.get(
                "rawTokenAmount",
                {}
            )
            .get(
                "tokenAmount"
            )
        )

        decimals = (
            output.get(
                "rawTokenAmount",
                {}
            )
            .get(
                "decimals"
            )
        )

        if amount is None:
            amount = output.get(
                "amount"
            )

        if decimals is None:
            decimals = 0

        try:

            raw_amount = Decimal(
                str(amount)
            )

            received_amount = (
                raw_amount
                / (
                    Decimal(10)
                    ** int(decimals)
                )
            )

        except Exception:

            continue

        received = {
            "amount": received_amount,
            "raw_amount": raw_amount,
            "decimals": int(
                decimals
            ),
        }

        break

    if not received:
        return None

    #
    # Determine the buyer.
    #
    # Helius can expose the fee payer and
    # account data. Prefer feePayer where available.
    #

    buyer = (
        transaction.get(
            "feePayer"
        )
        or transaction.get(
            "source"
        )
    )

    #
    # Determine quote value.
    #
    # Helius swap events can contain native SOL
    # or token input information.
    #

    spent_sol = Decimal("0")

    native_input = (
        token_swap.get(
            "nativeInput"
        )
    )

    if native_input:

        try:

            lamports = Decimal(
                str(
                    native_input
                    .get(
                        "amount",
                        0,
                    )
                )
            )

            spent_sol = (
                lamports
                / Decimal(
                    1_000_000_000
                )
            )

        except Exception:
            spent_sol = Decimal("0")

    #
    # Look for stablecoin input.
    #

    spent_token = Decimal("0")

    for item in token_inputs:

        amount = (
            item.get(
                "rawTokenAmount",
                {}
            )
            .get(
                "tokenAmount"
            )
        )

        decimals = (
            item.get(
                "rawTokenAmount",
                {}
            )
            .get(
                "decimals"
            )
        )

        if amount is None:
            amount = item.get(
                "amount"
            )

        if decimals is None:
            decimals = 0

        try:

            value = (
                Decimal(str(amount))
                / (
                    Decimal(10)
                    ** int(decimals)
                )
            )

        except Exception:

            continue

        spent_token = max(
            spent_token,
            value,
        )

    return {
        "buyer": buyer,
        "received_amount": received[
            "amount"
        ],
        "spent_sol": spent_sol,
        "spent_token": spent_token,
        "signature": transaction.get(
            "signature"
        )
        or transaction.get(
            "transactionSignature"
        ),
    }


# ============================================================
# PROCESS ONE SOLANA TOKEN
# ============================================================

async def process_token(
    token,
):

    token_address = token[
        "contract_address"
    ]

    transactions = (
        await get_token_transactions(
            token_address
        )
    )

    if not transactions:
        return

    group_id = int(
        token["group_id"]
    )

    settings = await get_buybot_settings(
        group_id
    )

    if not settings:
        return

    if not settings["enabled"]:
        return

    minimum_buy = Decimal(
        str(
            settings["min_buy_usd"]
            or 0
        )
    )

    #
    # Process the newest transactions first.
    #

    for transaction_summary in (
        transactions
    ):

        signature = (
            transaction_summary.get(
                "signature"
            )
        )

        if not signature:
            continue

        #
        # First duplicate check.
        #
        # This avoids requesting full transaction
        # data for transactions we've already handled.
        #

        if await event_exists(
            group_id,
            "SOL",
            signature,
            token_address,
        ):
            continue

        transaction = (
            await get_transaction(
                signature
            )
        )

        if not transaction:
            continue

        #
        # Ignore failed transactions.
        #

        if transaction.get(
            "transactionError"
        ):
            continue

        buy = extract_buy(
            transaction,
            token_address,
        )

        if not buy:
            continue

        #
        # Solana native SOL value isn't USD yet.
        #
        # We deliberately don't pretend SOL = USD.
        # The market-data layer will convert it later.
        #

        spent_usd = Decimal("0")

        if (
            minimum_buy > 0
            and spent_usd > 0
            and spent_usd < minimum_buy
        ):
            continue

        await save_event(
            group_id=group_id,
            chain="SOL",
            tx_hash=signature,
            token_address=token_address,
            token_symbol=token[
                "token_symbol"
            ],
            buyer_address=buy[
                "buyer"
            ],
            spent_amount_usd=spent_usd,
            received_amount=buy[
                "received_amount"
            ],
        )

        print(
            "SOLANA BUY DETECTED | "
            f"group={group_id} | "
            f"token={token['token_symbol']} | "
            f"received="
            f"{buy['received_amount']} | "
            f"buyer={buy['buyer']} | "
            f"tx={signature}"
        )


# ============================================================
# SOLANA WORKER
# ============================================================

async def solana_detector_worker():

    print(
        "Solana BuyBot detector worker "
        "started."
    )

    if not HELIUS_API_KEY:

        print(
            "Solana BuyBot disabled: "
            "HELIUS_API_KEY is missing."
        )

        while True:

            await asyncio.sleep(
                BUYBOT_POLL_SECONDS
            )

    while True:

        try:

            tokens = (
                await get_solana_tokens()
            )

            for token in tokens:

                try:

                    await process_token(
                        token
                    )

                except Exception as exc:

                    print(
                        "Solana token processing "
                        f"error: {exc}"
                    )

        except asyncio.CancelledError:

            print(
                "Solana BuyBot detector "
                "stopped."
            )

            raise

        except Exception as exc:

            print(
                "Solana BuyBot worker error: "
                f"{exc}"
            )

        await asyncio.sleep(
            BUYBOT_POLL_SECONDS
        )
