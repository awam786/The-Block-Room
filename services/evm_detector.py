import asyncio
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import (
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    ROBINHOOD_RPC_URL,
    BUYBOT_BLOCK_BATCH_SIZE,
    BUYBOT_POLL_SECONDS,
)

from database.connection import get_pool

from services.buybot_alert import (
    send_buy_alert,
)

from services.market_data import (
    get_market_data_safe,
    native_amount_to_usd,
    quote_amount_to_usd,
)


SWAP_TOPIC = (
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
)


CHAIN_CONFIG = {
    "bnb": {
        "rpc": BNB_RPC_URL,
        "explorer": "https://bscscan.com/tx/",
    },
    "ethereum": {
        "rpc": ETHEREUM_RPC_URL,
        "explorer": "https://etherscan.io/tx/",
    },
    "robinhood": {
        "rpc": ROBINHOOD_RPC_URL,
        "explorer": "https://explorer.mainnet.hoodscan.io/tx/",
    },
}


ERC20_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)


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
        "eth": "ethereum",
        "rh": "robinhood",
        "robinhood-chain": "robinhood",
    }

    return aliases.get(
        value,
        value,
    )


def clean_address(
    address: Optional[str],
) -> str:
    if not address:
        return ""

    return str(address).lower()


def topic_address(
    topic: str,
) -> str:
    if not topic:
        return ""

    value = topic.lower()

    if value.startswith("0x"):
        value = value[2:]

    return (
        "0x"
        + value[-40:]
    )


def topic_uint256(
    topic: str,
) -> int:
    if not topic:
        return 0

    try:
        value = topic

        if value.startswith("0x"):
            value = value[2:]

        return int(
            value,
            16,
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0


def data_uint256(
    data: str,
) -> int:
    if not data:
        return 0

    try:
        value = data

        if value.startswith("0x"):
            value = value[2:]

        if len(value) < 64:
            return 0

        return int(
            value[:64],
            16,
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0


async def rpc_request(
    rpc_url: str,
    method: str,
    params: List[Any],
) -> Optional[Any]:
    try:
        async with httpx.AsyncClient(
            timeout=20
        ) as client:
            response = await client.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params,
                },
            )

            response.raise_for_status()

            result = response.json()

            if "error" in result:
                print(
                    "RPC error "
                    f"method={method}: "
                    f"{result['error']}"
                )
                return None

            return result.get(
                "result"
            )

    except Exception as exc:
        print(
            "RPC request failed "
            f"method={method}: {exc}"
        )
        return None


async def get_latest_block(
    rpc_url: str,
) -> Optional[int]:
    result = await rpc_request(
        rpc_url,
        "eth_blockNumber",
        [],
    )

    if not result:
        return None

    try:
        return int(
            result,
            16,
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


async def get_logs(
    rpc_url: str,
    from_block: int,
    to_block: int,
) -> List[Dict[str, Any]]:
    if from_block > to_block:
        return []

    result = await rpc_request(
        rpc_url,
        "eth_getLogs",
        [
            {
                "fromBlock": hex(
                    from_block
                ),
                "toBlock": hex(
                    to_block
                ),
                "topics": [
                    SWAP_TOPIC
                ],
            }
        ],
    )

    if not isinstance(
        result,
        list,
    ):
        return []

    return result


async def get_token_decimals(
    rpc_url: str,
    token_address: str,
) -> int:
    result = await rpc_request(
        rpc_url,
        "eth_call",
        [
            {
                "to": token_address,
                "data": "0x313ce567",
            },
            "latest",
        ],
    )

    if not result:
        return 18

    try:
        return int(
            result,
            16,
        )

    except (
        TypeError,
        ValueError,
    ):
        return 18


async def get_transaction(
    rpc_url: str,
    tx_hash: str,
) -> Optional[Dict[str, Any]]:
    result = await rpc_request(
        rpc_url,
        "eth_getTransactionByHash",
        [tx_hash],
    )

    if not isinstance(
        result,
        dict,
    ):
        return None

    return result


async def get_transaction_receipt(
    rpc_url: str,
    tx_hash: str,
) -> Optional[Dict[str, Any]]:
    result = await rpc_request(
        rpc_url,
        "eth_getTransactionReceipt",
        [tx_hash],
    )

    if not isinstance(
        result,
        dict,
    ):
        return None

    return result


async def get_monitored_tokens(
    chain: str,
) -> List[Dict[str, Any]]:
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
              AND LOWER(chain) = LOWER($1)
            ORDER BY id ASC;
            """,
            chain,
        )

    return [
        dict(row)
        for row in rows
    ]


async def get_last_scanned_block(
    chain: str,
    latest_block: int,
) -> int:
    pool = await get_pool()

    key = (
        f"buybot_last_block_{chain}"
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
        return max(
            0,
            latest_block - 2,
        )

    try:
        return int(
            row["value"]
        )

    except (
        TypeError,
        ValueError,
    ):
        return max(
            0,
            latest_block - 2,
        )


async def save_last_scanned_block(
    chain: str,
    block_number: int,
):
    pool = await get_pool()

    key = (
        f"buybot_last_block_{chain}"
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
            str(block_number),
        )


async def event_already_processed(
    group_id: int,
    chain: str,
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
              AND LOWER(chain) = LOWER($2)
              AND LOWER(tx_hash) = LOWER($3)
              AND LOWER(token_address) = LOWER($4)
            LIMIT 1;
            """,
            group_id,
            chain,
            tx_hash,
            token_address,
        )

    return row is not None


async def save_buy_event(
    group_id: int,
    chain: str,
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


def parse_swap_log(
    log: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    topics = log.get(
        "topics",
        [],
    )

    if len(topics) < 3:
        return None

    if (
        str(topics[0]).lower()
        != SWAP_TOPIC.lower()
    ):
        return None

    sender = topic_address(
        topics[1]
    )

    recipient = topic_address(
        topics[2]
    )

    data = log.get(
        "data",
        "0x",
    )

    if not data.startswith(
        "0x"
    ):
        return None

    raw = data[2:]

    if len(raw) < 256:
        return None

    try:
        amount0_in = int(
            raw[0:64],
            16,
        )

        amount1_in = int(
            raw[64:128],
            16,
        )

        amount0_out = int(
            raw[128:192],
            16,
        )

        amount1_out = int(
            raw[192:256],
            16,
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    return {
        "sender": sender,
        "recipient": recipient,
        "amount0_in": amount0_in,
        "amount1_in": amount1_in,
        "amount0_out": amount0_out,
        "amount1_out": amount1_out,
        "pair_address": topic_address(
            log.get(
                "address",
                "",
            )
        ),
        "tx_hash": log.get(
            "transactionHash"
        ),
        "block_number": int(
            log.get(
                "blockNumber",
                "0x0",
            ),
            16,
        ),
    }


async def identify_buy(
    rpc_url: str,
    chain: str,
    token_address: str,
    log: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    parsed = parse_swap_log(
        log
    )

    if not parsed:
        return None

    token_address_clean = (
        clean_address(
            token_address
        )
    )

    pair_address = (
        parsed["pair_address"]
    )

    receipt = await get_transaction_receipt(
        rpc_url,
        parsed["tx_hash"],
    )

    if not receipt:
        return None

    logs = receipt.get(
        "logs",
        [],
    )

    token_in = 0
    token_out = 0

    quote_candidates = []

    for receipt_log in logs:
        receipt_address = clean_address(
            receipt_log.get(
                "address"
            )
        )

        receipt_topics = receipt_log.get(
            "topics",
            []
        )

        if len(receipt_topics) < 3:
            continue

        if (
            str(receipt_topics[0]).lower()
            != ERC20_TRANSFER_TOPIC.lower()
        ):
            continue

        from_address = topic_address(
            receipt_topics[1]
        )

        to_address = topic_address(
            receipt_topics[2]
        )

        amount = data_uint256(
            receipt_log.get(
                "data",
                "0x",
            )
        )

        if (
            receipt_address
            == token_address_clean
        ):
            if (
                to_address
                == pair_address
            ):
                token_in += amount

            elif (
                from_address
                == pair_address
            ):
                token_out += amount

    if token_out <= 0:
        return None

    if token_in <= 0:
        return None

    decimals = await get_token_decimals(
        rpc_url,
        token_address,
    )

    received_amount = (
        token_out
        / (10 ** decimals)
    )

    # The actual quote token is inferred from the
    # opposite side of the pair through market data.
    market_data = await get_market_data_safe(
        chain,
        token_address,
    )

    if not market_data:
        return None

    quote_token = (
        market_data.get(
            "quote_token"
        )
        or {}
    )

    quote_address = quote_token.get(
        "address"
    )

    if not quote_address:
        return None

    quote_decimals = 18

    if (
        clean_address(
            quote_address
        )
        != clean_address(
            token_address
        )
    ):
        quote_decimals = await get_token_decimals(
            rpc_url,
            quote_address,
        )

    # Determine the amount paid from the Swap event.
    parsed = parse_swap_log(
        log
    )

    if not parsed:
        return None

    spent_raw = 0

    # If token is amount0, amount1 is the quote.
    # Otherwise amount0 is the quote.
    #
    # We determine the position using the pair's
    # base/quote addresses when available.
    base_address = clean_address(
        market_data.get(
            "address"
        )
    )

    quote_is_token0 = (
        clean_address(
            quote_address
        )
        != clean_address(
            base_address
        )
    )

    if (
        clean_address(
            base_address
        )
        == token_address_clean
    ):
        spent_raw = parsed[
            "amount1_in"
        ]
    else:
        spent_raw = parsed[
            "amount0_in"
        ]

    if spent_raw <= 0:
        return None

    spent_amount = (
        spent_raw
        / (10 ** quote_decimals)
    )

    spent_usd = await quote_amount_to_usd(
        chain,
        quote_address,
        spent_amount,
    )

    # Some pools may not expose the quote correctly.
    # Fall back to native conversion when the quote is
    # the chain's wrapped native asset.
    if spent_usd <= 0:
        wrapped = {
            "bnb": (
                "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
            ),
            "ethereum": (
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
            ),
            "robinhood": (
                "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
            ),
        }.get(
            chain
        )

        if (
            wrapped
            and clean_address(
                quote_address
            )
            == clean_address(
                wrapped
            )
        ):
            spent_usd = await native_amount_to_usd(
                chain,
                spent_amount,
            )

    if spent_usd <= 0:
        return None

    buyer = (
        parsed.get(
            "sender"
        )
        or parsed.get(
            "recipient"
        )
        or ""
    )

    return {
        "tx_hash": parsed[
            "tx_hash"
        ],
        "pair_address": pair_address,
        "buyer_address": buyer,
        "received_amount": received_amount,
        "spent_amount": spent_amount,
        "spent_amount_usd": spent_usd,
        "quote_address": quote_address,
    }


async def process_log_for_token(
    chain: str,
    rpc_url: str,
    token: Dict[str, Any],
    log: Dict[str, Any],
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

    tx_hash = log.get(
        "transactionHash"
    )

    if not tx_hash:
        return

    if await event_already_processed(
        group_id,
        chain,
        tx_hash,
        token_address,
    ):
        return

    buy = await identify_buy(
        rpc_url,
        chain,
        token_address,
        log,
    )

    if not buy:
        return

    market_data = await get_market_data_safe(
        chain,
        token_address,
    )

    if not market_data:
        return

    try:
        sent = await send_buy_alert(
            group_id=group_id,
            chain=chain,
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
            spent_amount_usd=buy[
                "spent_amount_usd"
            ],
            received_amount=buy[
                "received_amount"
            ],
            buyer_address=buy[
                "buyer_address"
            ],
            tx_hash=tx_hash,
            market_data=market_data,
        )

        if not sent:
            return

    except Exception as exc:
        print(
            "Buy alert error "
            f"group={group_id} "
            f"tx={tx_hash}: {exc}"
        )
        return

    await save_buy_event(
        group_id=group_id,
        chain=chain,
        tx_hash=tx_hash,
        token_address=token_address,
        token_symbol=(
            token_symbol
            or market_data.get(
                "symbol"
            )
            or "TOKEN"
        ),
        buyer_address=buy[
            "buyer_address"
        ],
        spent_amount_usd=buy[
            "spent_amount_usd"
        ],
        received_amount=buy[
            "received_amount"
        ],
    )


async def scan_chain_once(
    chain: str,
):
    chain = normalize_chain(
        chain
    )

    config = CHAIN_CONFIG.get(
        chain
    )

    if not config:
        return

    rpc_url = config[
        "rpc"
    ]

    latest_block = await get_latest_block(
        rpc_url
    )

    if latest_block is None:
        return

    tokens = await get_monitored_tokens(
        chain
    )

    if not tokens:
        await save_last_scanned_block(
            chain,
            latest_block,
        )
        return

    last_block = await get_last_scanned_block(
        chain,
        latest_block,
    )

    from_block = last_block + 1

    if from_block > latest_block:
        return

    max_to_block = (
        from_block
        + BUYBOT_BLOCK_BATCH_SIZE
        - 1
    )

    to_block = min(
        max_to_block,
        latest_block,
    )

    logs = await get_logs(
        rpc_url,
        from_block,
        to_block,
    )

    if logs:
        for log in logs:
            try:
                for token in tokens:
                    await process_log_for_token(
                        chain,
                        rpc_url,
                        token,
                        log,
                    )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                print(
                    "EVM log processing error "
                    f"chain={chain}: {exc}"
                )

    await save_last_scanned_block(
        chain,
        to_block,
    )


async def evm_chain_worker(
    chain: str,
):
    print(
        f"{chain.upper()} BuyBot detector started."
    )

    while True:
        try:
            await scan_chain_once(
                chain
            )

        except asyncio.CancelledError:
            print(
                f"{chain.upper()} BuyBot detector "
                "stopping..."
            )
            raise

        except Exception as exc:
            print(
                f"{chain.upper()} detector error: "
                f"{exc}"
            )

        await asyncio.sleep(
            BUYBOT_POLL_SECONDS
        )


async def evm_detector_worker():
    await asyncio.gather(
        evm_chain_worker(
            "bnb"
        ),
        evm_chain_worker(
            "ethereum"
        ),
        evm_chain_worker(
            "robinhood"
        ),
    )
