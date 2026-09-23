from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
from telegram import Bot

from config import (
    HELIUS_API_KEY,
    BUYBOT_POLL_SECONDS,
    BOT_TOKEN,
)

from database.connection import get_pool

from services.buybot_alert import (
    send_buy_alert,
)


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
# DATABASE
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


async def get_buybot_settings(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:

        return await connection.fetchrow(
            """
            SELECT
                enabled,
                min_buy_usd
            FROM buybot_settings
            WHERE group_id = $1
            """,
            group_id,
        )


# ============================================================
# HELIUS API
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
# SOLANA BUY EXTRACTION
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
        {},
    )

    if not isinstance(
        token_swap,
        dict,
    ):
        return None

    token_inputs = (
        token_swap.get(
            "tokenInputs",
            [],
        )
        or []
    )

    token_outputs = (
        token_swap.get(
            "tokenOutputs",
            [],
        )
        or []
    )

    # ========================================================
    # FIND MONITORED TOKEN OUTPUT
    # ========================================================

    received = None

    for output in token_outputs:

        if not isinstance(
            output,
            dict,
        ):
            continue

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

        if (
            str(mint).lower()
            != token_address
        ):
            continue

        raw_token_amount = (
            output.get(
                "rawTokenAmount",
                {},
            )
            or {}
        )

        amount = (
            raw_token_amount.get(
                "tokenAmount"
            )
        )

        decimals = (
            raw_token_amount.get(
                "decimals"
            )
        )

        if amount is None:

            amount = output.get(
                "amount"
            )

        if decimals is None:

            decimals = output.get(
                "decimals",
                0,
            )

        try:

            raw_amount = Decimal(
                str(amount)
            )

            decimal_places = int(
                decimals
            )

            received_amount = (
                raw_amount
                / (
                    Decimal(10)
                    ** decimal_places
                )
            )

        except Exception:

            continue

        received = {
            "amount": received_amount,
            "raw_amount": raw_amount,
            "decimals": decimal_places,
        }

        break

    if not received:
        return None

    # ========================================================
    # BUYER
    # ========================================================

    buyer = (
        transaction.get(
            "feePayer"
        )
        or transaction.get(
            "source"
        )
    )

    # ========================================================
    # NATIVE SOL INPUT
    # ========================================================

    spent_sol = Decimal(
        "0"
    )

    native_input = (
        token_swap.get(
            "nativeInput"
        )
    )

    if isinstance(
        native_input,
        dict,
    ):

        try:

            lamports = Decimal(
                str(
                    native_input.get(
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

            spent_sol = Decimal(
                "0"
            )

    # ========================================================
    # TOKEN INPUT
    # ========================================================

    spent_token = Decimal(
        "0"
    )

    for item in token_inputs:

        if not isinstance(
            item,
            dict,
        ):
            continue

        raw_token_amount = (
            item.get(
                "rawTokenAmount",
                {},
            )
            or {}
        )

        amount = (
            raw_token_amount.get(
                "tokenAmount"
            )
        )

        decimals = (
            raw_token_amount.get(
                "decimals"
            )
        )

        if amount is None:

            amount = item.get(
                "amount"
            )

        if decimals is None:

            decimals = item.get(
                "decimals",
                0,
            )

        try:

            value = (
                Decimal(
                    str(amount)
                )
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

    # ========================================================
    # SIGNATURE
    # ========================================================

    signature = (
        transaction.get(
            "signature"
        )
        or transaction.get(
            "transactionSignature"
        )
    )

    if not signature:
        return None

    return {
        "buyer": buyer,
        "received_amount": (
            received["amount"]
        ),
        "spent_sol": spent_sol,
        "spent_token": spent_token,
        "signature": signature,
    }


# ============================================================
# PROCESS ONE TOKEN
# ============================================================

async def process_token(
    token,
):
    token_address = str(
        token[
            "contract_address"
        ]
    )

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

    settings = (
        await get_buybot_settings(
            group_id
        )
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

    for transaction_summary in (
        transactions
    ):

        if not isinstance(
            transaction_summary,
            dict,
        ):
            continue

        signature = (
            transaction_summary.get(
                "signature"
            )
            or transaction_summary.get(
                "transactionSignature"
            )
        )

        if not signature:
            continue

        # ----------------------------------------------------
        # DUPLICATE PROTECTION
        # ----------------------------------------------------

        if await event_exists(
            group_id=group_id,
            chain="SOL",
            tx_hash=signature,
            token_address=token_address,
        ):
            continue

        # ----------------------------------------------------
        # FETCH PARSED TRANSACTION
        # ----------------------------------------------------

        transaction = (
            await get_transaction(
                signature
            )
        )

        if not transaction:
            continue

        if transaction.get(
            "transactionError"
        ):
            continue

        # ----------------------------------------------------
        # EXTRACT BUY
        # ----------------------------------------------------

        buy = extract_buy(
            transaction=transaction,
            token_address=token_address,
        )

        if not buy:
            continue

        # ----------------------------------------------------
        # USD VALUE
        # ----------------------------------------------------
        #
        # At this stage we know the SOL amount,
        # but we do not yet have a SOL/USD price.
        #
        # Therefore do NOT falsely label SOL as USD.
        #
        # The upcoming market-price service will
        # calculate this properly.
        #

        spent_usd = None

        # ----------------------------------------------------
        # MINIMUM BUY FILTER
        # ----------------------------------------------------
        #
        # Since USD conversion is not available yet,
        # don't reject the transaction using a fake
        # $0 value.
        #

        if (
            minimum_buy > 0
            and spent_usd is not None
            and spent_usd < minimum_buy
        ):
            continue

        # ----------------------------------------------------
        # SAVE EVENT
        # ----------------------------------------------------

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
            spent_amount_usd=(
                Decimal("0")
            ),
            received_amount=buy[
                "received_amount"
            ],
        )

        # ----------------------------------------------------
        # SEND SHARED ALERT
        # ----------------------------------------------------

        try:

            bot = Bot(
                token=BOT_TOKEN
            )

            try:

                await send_buy_alert(
                    bot=bot,
                    group_id=group_id,
                    chain="SOL",
                    token_address=token_address,
                    token_name=token[
                        "token_name"
                    ],
                    token_symbol=token[
                        "token_symbol"
                    ],
                    buyer_address=buy[
                        "buyer"
                    ],
                    spent_amount_usd=None,
                    received_amount=buy[
                        "received_amount"
                    ],
                    tx_hash=signature,
                    market_cap_usd=None,
                    dex_url=token[
                        "dex_url"
                    ],
                    trending_url=None,
                )

            finally:

                await bot.shutdown()

        except Exception as exc:

            print(
                "Failed sending Solana "
                "BuyBot alert: "
                f"{exc}"
            )

        print(
            "SOLANA BUY DETECTED | "
            f"group={group_id} | "
            f"token={token['token_symbol']} | "
            f"spent_sol="
            f"{buy['spent_sol']} | "
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
