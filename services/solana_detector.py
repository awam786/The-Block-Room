import asyncio
from typing import Any, Dict, List, Optional

import httpx

from config import HELIUS_API_KEY, BUYBOT_POLL_SECONDS

from database.connection import get_pool

from services.buybot_alert import (
    send_buy_alert,
)

from services.market_data import (
    get_market_data_safe,
    get_sol_price_usd,
)


HELIUS_RPC_URL = (
    "https://mainnet.helius-rpc.com/"
)

HELIUS_API_URL = (
    "https://api.helius.xyz"
)


CHAIN = "solana"


async def helius_rpc(
    method: str,
    params: List[Any],
) -> Optional[Any]:
    if not HELIUS_API_KEY:
        print(
            "HELIUS_API_KEY is not configured."
        )
        return None

    url = (
        f"{HELIUS_RPC_URL}"
        f"?api-key={HELIUS_API_KEY}"
    )

    payload = {
        "jsonrpc": "2.0",
        "id": "the-block-room",
        "method": method,
        "params": params,
    }

    try:
        async with httpx.AsyncClient(
            timeout=25
        ) as client:
            response = await client.post(
                url,
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

            if "error" in data:
                print(
                    "Helius RPC error "
                    f"method={method}: "
                    f"{data['error']}"
                )
                return None

            return data.get(
                "result"
            )

    except Exception as exc:
        print(
            "Helius RPC request error "
            f"method={method}: {exc}"
        )
        return None


async def get_monitored_tokens() -> List[Dict[str, Any]]:
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
                token_symbol
            FROM buybot_tokens
            WHERE enabled = TRUE
              AND LOWER(chain) = 'solana'
            ORDER BY id ASC;
            """
        )

    return [
        dict(row)
        for row in rows
    ]


async def get_last_signature(
    group_id: int,
    token_address: str,
) -> Optional[str]:
    pool = await get_pool()

    key = (
        "buybot_solana_last_signature:"
        f"{group_id}:"
        f"{token_address.lower()}"
    )

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT value
            FROM system_settings
            WHERE key = $1;
            """,
            key,
        )

    if not row:
        return None

    return row["value"]


async def save_last_signature(
    group_id: int,
    token_address: str,
    signature: str,
):
    pool = await get_pool()

    key = (
        "buybot_solana_last_signature:"
        f"{group_id}:"
        f"{token_address.lower()}"
    )

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO system_settings (
                key,
                value
            )
            VALUES ($1, $2)
            ON CONFLICT (key)
            DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = NOW();
            """,
            key,
            signature,
        )


async def get_token_signatures(
    token_address: str,
    before: Optional[str] = None,
    limit: int = 100,
) -> List[str]:
    params = [
        token_address,
        {
            "limit": limit,
        },
    ]

    if before:
        params[1]["before"] = before

    result = await helius_rpc(
        "getSignaturesForAddress",
        params,
    )

    if not isinstance(
        result,
        list,
    ):
        return []

    signatures = []

    for item in result:
        if not isinstance(
            item,
            dict,
        ):
            continue

        signature = item.get(
            "signature"
        )

        if signature:
            signatures.append(
                signature
            )

    return signatures


async def get_parsed_transaction(
    signature: str,
) -> Optional[Dict[str, Any]]:
    result = await helius_rpc(
        "getTransaction",
        [
            signature,
            {
                "encoding": "jsonParsed",
                "commitment": "finalized",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )

    if not isinstance(
        result,
        dict,
    ):
        return None

    return result


def is_failed_transaction(
    transaction: Dict[str, Any],
) -> bool:
    meta = transaction.get(
        "meta"
    )

    if not isinstance(
        meta,
        dict,
    ):
        return True

    return meta.get(
        "err"
    ) is not None


def get_account_keys(
    transaction: Dict[str, Any],
) -> List[str]:
    message = (
        transaction
        .get("transaction", {})
        .get("message", {})
    )

    account_keys = message.get(
        "accountKeys",
        [],
    )

    result = []

    for item in account_keys:
        if isinstance(
            item,
            str,
        ):
            result.append(
                item
            )
            continue

        if isinstance(
            item,
            dict,
        ):
            pubkey = item.get(
                "pubkey"
            )

            if pubkey:
                result.append(
                    pubkey
                )

    return result


def extract_token_balance_changes(
    transaction: Dict[str, Any],
    token_address: str,
) -> List[Dict[str, Any]]:
    meta = transaction.get(
        "meta",
        {}
    )

    pre_balances = meta.get(
        "preTokenBalances",
        []
    )

    post_balances = meta.get(
        "postTokenBalances",
        []
    )

    pre_map = {}
    post_map = {}

    for balance in pre_balances:
        if not isinstance(
            balance,
            dict,
        ):
            continue

        if balance.get(
            "mint"
        ) != token_address:
            continue

        index = balance.get(
            "accountIndex"
        )

        amount = (
            balance
            .get("uiTokenAmount", {})
            .get("uiAmount")
        )

        if amount is None:
            amount = 0

        pre_map[index] = float(
            amount
        )

    for balance in post_balances:
        if not isinstance(
            balance,
            dict,
        ):
            continue

        if balance.get(
            "mint"
        ) != token_address:
            continue

        index = balance.get(
            "accountIndex"
        )

        amount = (
            balance
            .get("uiTokenAmount", {})
            .get("uiAmount")
        )

        if amount is None:
            amount = 0

        post_map[index] = float(
            amount
        )

    changes = []

    all_indexes = (
        set(pre_map)
        | set(post_map)
    )

    for index in all_indexes:
        before = pre_map.get(
            index,
            0.0,
        )

        after = post_map.get(
            index,
            0.0,
        )

        delta = after - before

        if delta <= 0:
            continue

        changes.append(
            {
                "account_index": index,
                "amount": delta,
            }
        )

    return changes


def find_buyer_for_token_change(
    transaction: Dict[str, Any],
    account_index: Optional[int],
) -> str:
    if account_index is None:
        return ""

    keys = get_account_keys(
        transaction
    )

    if (
        account_index >= 0
        and account_index < len(keys)
    ):
        return keys[
            account_index
        ]

    return ""


def extract_sol_spent(
    transaction: Dict[str, Any],
    buyer_address: str,
) -> float:
    meta = transaction.get(
        "meta",
        {}
    )

    pre_balances = meta.get(
        "preBalances",
        []
    )

    post_balances = meta.get(
        "postBalances",
        []
    )

    keys = get_account_keys(
        transaction
    )

    if not buyer_address:
        return 0.0

    try:
        buyer_index = keys.index(
            buyer_address
        )

    except ValueError:
        return 0.0

    if (
        buyer_index >= len(
            pre_balances
        )
        or buyer_index >= len(
            post_balances
        )
    ):
        return 0.0

    pre = float(
        pre_balances[
            buyer_index
        ]
    )

    post = float(
        post_balances[
            buyer_index
        ]
    )

    # Lamports -> SOL.
    #
    # A buyer normally loses SOL for the swap,
    # but transaction fees also reduce the balance.
    # We use the actual balance reduction as the
    # conservative amount spent.
    spent_lamports = (
        pre - post
    )

    if spent_lamports <= 0:
        return 0.0

    return (
        spent_lamports
        / 1_000_000_000
    )


def extract_token_received_amount(
    transaction: Dict[str, Any],
    token_address: str,
) -> float:
    changes = extract_token_balance_changes(
        transaction,
        token_address,
    )

    if not changes:
        return 0.0

    return max(
        change["amount"]
        for change in changes
    )


def find_best_token_buyer(
    transaction: Dict[str, Any],
    token_address: str,
) -> Dict[str, Any]:
    changes = extract_token_balance_changes(
        transaction,
        token_address,
    )

    if not changes:
        return {
            "buyer": "",
            "amount": 0.0,
        }

    best = max(
        changes,
        key=lambda item: item[
            "amount"
        ],
    )

    buyer = find_buyer_for_token_change(
        transaction,
        best.get(
            "account_index"
        ),
    )

    return {
        "buyer": buyer,
        "amount": best[
            "amount"
        ],
    }


async def event_already_processed(
    group_id: int,
    tx_hash: str,
    token_address: str,
) -> bool:
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT id
            FROM buybot_events
            WHERE group_id = $1
              AND LOWER(chain) = 'solana'
              AND LOWER(tx_hash) = LOWER($2)
              AND LOWER(token_address) = LOWER($3)
            LIMIT 1;
            """,
            group_id,
            tx_hash,
            token_address,
        )

    return row is not None


async def save_buy_event(
    group_id: int,
    tx_hash: str,
    token_address: str,
    token_symbol: str,
    buyer_address: str,
    spent_amount_usd: float,
    received_amount: float,
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
                'solana',
                $2,
                $3,
                $4,
                $5,
                $6,
                $7
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
            tx_hash,
            token_address,
            token_symbol,
            buyer_address,
            spent_amount_usd,
            received_amount,
        )


async def process_transaction(
    token: Dict[str, Any],
    signature: str,
):
    group_id = token[
        "group_id"
    ]

    token_address = token[
        "contract_address"
    ]

    token_symbol = (
        token.get(
            "token_symbol"
        )
        or "TOKEN"
    )

    if await event_already_processed(
        group_id,
        signature,
        token_address,
    ):
        return

    transaction = await get_parsed_transaction(
        signature
    )

    if not transaction:
        return

    if is_failed_transaction(
        transaction
    ):
        return

    buyer_info = find_best_token_buyer(
        transaction,
        token_address,
    )

    buyer_address = buyer_info.get(
        "buyer"
    )

    received_amount = buyer_info.get(
        "amount",
        0.0,
    )

    if not buyer_address:
        return

    if received_amount <= 0:
        return

    sol_spent = extract_sol_spent(
        transaction,
        buyer_address,
    )

    if sol_spent <= 0:
        return

    sol_price = await get_sol_price_usd()

    if sol_price <= 0:
        return

    spent_usd = (
        sol_spent
        * sol_price
    )

    if spent_usd <= 0:
        return

    market_data = await get_market_data_safe(
        "solana",
        token_address,
    )

    if not market_data:
        return

    try:
        sent = await send_buy_alert(
            group_id=group_id,
            chain="solana",
            token_name=(
                token.get(
                    "token_name"
                )
                or market_data.get(
                    "name"
                )
                or "Unknown"
            ),
            token_symbol=(
                token_symbol
                or market_data.get(
                    "symbol"
                )
                or "TOKEN"
            ),
            spent_amount_usd=spent_usd,
            received_amount=received_amount,
            buyer_address=buyer_address,
            tx_hash=signature,
            market_data=market_data,
        )

        if not sent:
            return

    except Exception as exc:
        print(
            "Solana BuyBot alert error "
            f"group={group_id} "
            f"tx={signature}: {exc}"
        )
        return

    await save_buy_event(
        group_id=group_id,
        tx_hash=signature,
        token_address=token_address,
        token_symbol=(
            token_symbol
            or market_data.get(
                "symbol"
            )
            or "TOKEN"
        ),
        buyer_address=buyer_address,
        spent_amount_usd=spent_usd,
        received_amount=received_amount,
    )


async def process_token(
    token: Dict[str, Any],
):
    group_id = token[
        "group_id"
    ]

    token_address = token[
        "contract_address"
    ]

    last_signature = await get_last_signature(
        group_id,
        token_address,
    )

    signatures = await get_token_signatures(
        token_address,
        limit=100,
    )

    if not signatures:
        return

    # getSignaturesForAddress returns newest first.
    #
    # Process oldest -> newest so alerts appear
    # in chronological order.
    if last_signature:
        if last_signature in signatures:
            index = signatures.index(
                last_signature
            )

            signatures = signatures[
                :index
            ]

    signatures.reverse()

    if not signatures:
        return

    newest_processed = None

    for signature in signatures:
        try:
            await process_transaction(
                token,
                signature,
            )

            newest_processed = signature

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                "Solana transaction error "
                f"token={token_address} "
                f"tx={signature}: "
                f"{exc}"
            )

    if newest_processed:
        await save_last_signature(
            group_id,
            token_address,
            newest_processed,
        )


async def solana_detector_worker():
    print(
        "Solana BuyBot detector started."
    )

    while True:
        try:
            tokens = await get_monitored_tokens()

            for token in tokens:
                try:
                    await process_token(
                        token
                    )

                except asyncio.CancelledError:
                    raise

                except Exception as exc:
                    print(
                        "Solana token processing "
                        f"error token="
                        f"{token.get('contract_address')}: "
                        f"{exc}"
                    )

        except asyncio.CancelledError:
            print(
                "Solana BuyBot detector "
                "stopping..."
            )
            raise

        except Exception as exc:
            print(
                "Solana BuyBot detector error: "
                f"{exc}"
            )

        await asyncio.sleep(
            BUYBOT_POLL_SECONDS
        )
