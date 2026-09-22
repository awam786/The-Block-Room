import asyncio
from decimal import Decimal
from typing import Optional

import httpx

from config import (
    HELIUS_API_KEY,
    BUYBOT_POLL_SECONDS,
)

from database.connection import get_pool

from services.buybot_events import BuyEvent

from services.buybot_dispatcher import (
    process_buy_event,
)


HELIUS_RPC_URL = (
    "https://mainnet.helius-rpc.com/"
)


# Common Solana quote assets.
SOL_MINT = (
    "So11111111111111111111111111111111111111112"
)

USDC_MINT = (
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
)

USDT_MINT = (
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
)


async def helius_rpc(
    method: str,
    params: list,
):
    if not HELIUS_API_KEY:
        raise RuntimeError(
            "HELIUS_API_KEY is not configured."
        )

    payload = {
        "jsonrpc": "2.0",
        "id": "the-block-room",
        "method": method,
        "params": params,
    }

    async with httpx.AsyncClient(
        timeout=20
    ) as client:
        response = await client.post(
            HELIUS_RPC_URL,
            params={
                "api-key": HELIUS_API_KEY,
            },
            json=payload,
        )

        response.raise_for_status()

        data = response.json()

    if data.get("error"):
        raise RuntimeError(
            data["error"].get(
                "message",
                "Helius RPC request failed.",
            )
        )

    return data.get("result")


async def get_monitored_tokens():
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                group_id,
                contract_address,
                token_name,
                token_symbol
            FROM buybot_tokens
            WHERE LOWER(chain) = 'solana'
              AND enabled = TRUE;
            """
        )

    return rows


async def get_token_metadata(
    mint_address: str,
):
    result = await helius_rpc(
        "getAsset",
        [
            {
                "id": mint_address,
            }
        ],
    )

    if not result:
        return {
            "name": "Unknown Token",
            "symbol": "TOKEN",
            "decimals": 6,
        }

    content = result.get(
        "content"
    ) or {}

    metadata = content.get(
        "metadata"
    ) or {}

    token_info = result.get(
        "token_info"
    ) or {}

    return {
        "name": (
            metadata.get("name")
            or "Unknown Token"
        ),
        "symbol": (
            metadata.get("symbol")
            or "TOKEN"
        ),
        "decimals": int(
            token_info.get(
                "decimals",
                6,
            )
        ),
    }


def raw_to_amount(
    raw_amount: int,
    decimals: int,
) -> Decimal:
    if raw_amount <= 0:
        return Decimal("0")

    return Decimal(
        raw_amount
    ) / (
        Decimal(10) ** decimals
    )


def get_account_token_balance(
    account: dict,
) -> Decimal:
    raw = account.get(
        "uiTokenAmount"
    ) or {}

    ui_amount = raw.get(
        "uiAmount"
    )

    if ui_amount is not None:
        return Decimal(
            str(ui_amount)
        )

    amount = int(
        raw.get(
            "amount",
            "0",
        )
    )

    decimals = int(
        raw.get(
            "decimals",
            0,
        )
    )

    return raw_to_amount(
        amount,
        decimals,
    )


def extract_token_changes(
    transaction: dict,
    mint_address: str,
):
    meta = transaction.get(
        "meta"
    ) or {}

    pre_balances = (
        meta.get(
            "preTokenBalances"
        )
        or []
    )

    post_balances = (
        meta.get(
            "postTokenBalances"
        )
        or []
    )

    pre_by_owner = {}
    post_by_owner = {}

    for item in pre_balances:
        if item.get("mint") != mint_address:
            continue

        owner = item.get(
            "owner"
        )

        if not owner:
            continue

        pre_by_owner[owner] = (
            get_account_token_balance(
                item
            )
        )

    for item in post_balances:
        if item.get("mint") != mint_address:
            continue

        owner = item.get(
            "owner"
        )

        if not owner:
            continue

        post_by_owner[owner] = (
            get_account_token_balance(
                item
            )
        )

    changes = []

    owners = set(
        pre_by_owner
    ) | set(
        post_by_owner
    )

    for owner in owners:
        pre = pre_by_owner.get(
            owner,
            Decimal("0"),
        )

        post = post_by_owner.get(
            owner,
            Decimal("0"),
        )

        difference = post - pre

        if difference == 0:
            continue

        changes.append(
            {
                "owner": owner,
                "pre": pre,
                "post": post,
                "difference": difference,
            }
        )

    return changes


def extract_sol_change(
    transaction: dict,
    account_keys: list,
    buyer_address: Optional[str],
):
    meta = transaction.get(
        "meta"
    ) or {}

    pre_balances = (
        meta.get(
            "preBalances"
        )
        or []
    )

    post_balances = (
        meta.get(
            "postBalances"
        )
        or []
    )

    if not buyer_address:
        return Decimal("0")

    for index, account in enumerate(
        account_keys
    ):
        if account != buyer_address:
            continue

        if index >= len(
            pre_balances
        ) or index >= len(
            post_balances
        ):
            return Decimal("0")

        difference = (
            post_balances[index]
            - pre_balances[index]
        )

        # Negative means SOL left the wallet.
        if difference < 0:
            return (
                Decimal(
                    abs(difference)
                )
                / Decimal(
                    1_000_000_000
                )
            )

    return Decimal("0")


def get_account_keys(
    transaction: dict,
):
    message = (
        transaction.get(
            "transaction"
        ) or {}
    ).get(
        "message"
    ) or {}

    keys = (
        message.get(
            "accountKeys"
        )
        or []
    )

    result = []

    for key in keys:
        if isinstance(
            key,
            str,
        ):
            result.append(
                key
            )
        elif isinstance(
            key,
            dict,
        ):
            pubkey = key.get(
                "pubkey"
            )

            if pubkey:
                result.append(
                    pubkey
                )

    return result


def find_buyer(
    changes: list,
):
    positive = [
        item
        for item in changes
        if item["difference"] > 0
    ]

    if not positive:
        return None

    positive.sort(
        key=lambda item: item[
            "difference"
        ],
        reverse=True,
    )

    return positive[0][
        "owner"
    ]


async def get_transaction(
    signature: str,
):
    return await helius_rpc(
        "getTransaction",
        [
            signature,
            {
                "encoding": "jsonParsed",
                "commitment": "confirmed",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )


async def get_signatures_for_address(
    address: str,
    before: Optional[str] = None,
):
    params = [
        address,
        {
            "limit": 20,
        },
    ]

    if before:
        params[1][
            "before"
        ] = before

    return await helius_rpc(
        "getSignaturesForAddress",
        params,
    )


async def build_buy_event(
    token_row,
    signature: str,
):
    mint_address = token_row[
        "contract_address"
    ]

    transaction = await get_transaction(
        signature
    )

    if not transaction:
        return None

    if (
        transaction.get(
            "meta"
        ) or {}
    ).get(
        "err"
    ):
        return None

    changes = extract_token_changes(
        transaction,
        mint_address,
    )

    buyer = find_buyer(
        changes
    )

    if not buyer:
        return None

    received = Decimal("0")

    for change in changes:
        if (
            change["owner"]
            == buyer
            and change["difference"]
            > 0
        ):
            received += change[
                "difference"
            ]

    if received <= 0:
        return None

    account_keys = get_account_keys(
        transaction
    )

    spent_sol = extract_sol_change(
        transaction,
        account_keys,
        buyer,
    )

    metadata = await get_token_metadata(
        mint_address
    )

    return BuyEvent(
        group_id=token_row[
            "group_id"
        ],
        chain="solana",
        tx_hash=signature,
        token_address=mint_address,
        token_name=(
            token_row[
                "token_name"
            ]
            or metadata[
                "name"
            ]
        ),
        token_symbol=(
            token_row[
                "token_symbol"
            ]
            or metadata[
                "symbol"
            ]
        ),
        buyer_address=buyer,
        spent_amount_usd=Decimal("0"),
        spent_native_amount=spent_sol,
        spent_native_symbol="SOL",
        received_amount=received,
        received_symbol=metadata[
            "symbol"
        ],
        market_cap_usd=Decimal("0"),
        is_new_holder=False,
        dex_url=None,
        buy_url=None,
        trending_url=None,
        block_number=None,
    )


async def process_signature_for_token(
    token_row,
    signature: str,
):
    try:
        event = await build_buy_event(
            token_row,
            signature,
        )

        if not event:
            return 0

        return await process_buy_event(
            event
        )

    except Exception as exc:
        print(
            "Solana BuyBot event error "
            f"token={token_row['contract_address']} "
            f"tx={signature}: "
            f"{exc}"
        )

        return 0


async def scan_token(
    token_row,
    last_signature: Optional[str],
):
    mint_address = token_row[
        "contract_address"
    ]

    signatures = (
        await get_signatures_for_address(
            mint_address
        )
    )

    if not signatures:
        return last_signature

    signatures = list(
        reversed(
            signatures
        )
    )

    newest_signature = (
        signatures[-1].get(
            "signature"
        )
        if signatures
        else last_signature
    )

    for item in signatures:
        signature = item.get(
            "signature"
        )

        if not signature:
            continue

        if (
            last_signature
            and signature
            == last_signature
        ):
            continue

        if item.get(
            "err"
        ):
            continue

        await process_signature_for_token(
            token_row,
            signature,
        )

    return newest_signature


async def solana_detector_worker():
    if not HELIUS_API_KEY:
        print(
            "Solana BuyBot detector "
            "disabled: HELIUS_API_KEY "
            "is missing."
        )
        return

    last_signatures = {}

    print(
        "Solana BuyBot detector started."
    )

    while True:
        try:
            tokens = (
                await get_monitored_tokens()
            )

            for token in tokens:
                key = (
                    f"{token['group_id']}:"
                    f"{token['contract_address']}"
                )

                previous = (
                    last_signatures.get(
                        key
                    )
                )

                newest = await scan_token(
                    token,
                    previous,
                )

                if newest:
                    last_signatures[
                        key
                    ] = newest

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                "Solana detector error: "
                f"{exc}"
            )

        await asyncio.sleep(
            BUYBOT_POLL_SECONDS
        )
