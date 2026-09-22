import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import httpx

from config import (
    BUYBOT_BLOCK_BATCH_SIZE,
    BUYBOT_POLL_SECONDS,
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    ROBINHOOD_RPC_URL,
)

from database.connection import get_pool

from services.buybot_dispatcher import (
    process_buy_event,
)

from services.buybot_events import (
    BuyEvent,
)


# ============================================================
# CONSTANTS
# ============================================================

SWAP_EVENT_TOPIC = (
    "0xd78ad95fa46c994b6551d0da85fc275fe613b0"
    "046efc9f1e2c8f3f7c8f7c7c"
)

# Standard Uniswap V2-style Swap event:
#
# Swap(
#     address indexed sender,
#     uint256 amount0In,
#     uint256 amount1In,
#     uint256 amount0Out,
#     uint256 amount1Out,
#     address indexed to
# )
#
# Topic 0:
# keccak256("Swap(address,uint256,uint256,uint256,uint256,address)")


@dataclass
class EVMChain:
    name: str
    rpc_url: str
    native_symbol: str
    chain_id: int


CHAINS = {
    "ethereum": EVMChain(
        name="ethereum",
        rpc_url=ETHEREUM_RPC_URL,
        native_symbol="ETH",
        chain_id=1,
    ),

    "bnb": EVMChain(
        name="bnb",
        rpc_url=BNB_RPC_URL,
        native_symbol="BNB",
        chain_id=56,
    ),

    "robinhood": EVMChain(
        name="robinhood",
        rpc_url=ROBINHOOD_RPC_URL,
        native_symbol="ETH",
        chain_id=4663,
    ),
}


# ============================================================
# JSON-RPC
# ============================================================

async def rpc_call(
    client: httpx.AsyncClient,
    rpc_url: str,
    method: str,
    params: list,
):
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": method,
        "params": params,
    }

    response = await client.post(
        rpc_url,
        json=payload,
    )

    response.raise_for_status()

    data = response.json()

    if data.get("error"):
        raise RuntimeError(
            str(data["error"])
        )

    return data.get("result")


async def get_block_number(
    client: httpx.AsyncClient,
    rpc_url: str,
) -> int:

    result = await rpc_call(
        client,
        rpc_url,
        "eth_blockNumber",
        [],
    )

    return int(
        result,
        16,
    )


async def get_logs(
    client: httpx.AsyncClient,
    rpc_url: str,
    from_block: int,
    to_block: int,
    addresses: list[str],
):
    if not addresses:
        return []

    params = [
        {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": addresses,
            "topics": [
                SWAP_EVENT_TOPIC
            ],
        }
    ]

    result = await rpc_call(
        client,
        rpc_url,
        "eth_getLogs",
        params,
    )

    return result or []


# ============================================================
# TOKEN LOOKUP
# ============================================================

async def get_monitored_pairs(
    chain: str,
):
    pool = get_pool()

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT
                group_id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                pair_address,
                dex_url
            FROM buybot_tokens
            WHERE LOWER(chain) = LOWER($1)
              AND enabled = TRUE
              AND pair_address IS NOT NULL
              AND pair_address <> '';
            """,
            chain,
        )

    return rows


# ============================================================
# HEX HELPERS
# ============================================================

def hex_to_int(
    value: str,
) -> int:

    if not value:
        return 0

    return int(
        value,
        16,
    )


def decode_word(
    data: str,
    index: int,
) -> int:

    clean = data[2:] if data.startswith("0x") else data

    start = index * 64
    end = start + 64

    if len(clean) < end:
        return 0

    return int(
        clean[start:end],
        16,
    )


def decode_address_from_topic(
    topic: str,
) -> str:

    clean = topic[2:] if topic.startswith("0x") else topic

    return (
        "0x"
        + clean[-40:]
    )


def decode_swap_log(
    log: dict,
) -> Optional[dict]:

    topics = log.get("topics") or []

    data = log.get("data") or "0x"

    if len(topics) < 3:
        return None

    try:

        sender = decode_address_from_topic(
            topics[1]
        )

        recipient = decode_address_from_topic(
            topics[2]
        )

        amount0_in = decode_word(
            data,
            0,
        )

        amount1_in = decode_word(
            data,
            1,
        )

        amount0_out = decode_word(
            data,
            2,
        )

        amount1_out = decode_word(
            data,
            3,
        )

        return {
            "sender": sender,
            "recipient": recipient,
            "amount0_in": amount0_in,
            "amount1_in": amount1_in,
            "amount0_out": amount0_out,
            "amount1_out": amount1_out,
        }

    except (
        ValueError,
        IndexError,
    ):

        return None


# ============================================================
# EVENT CLASSIFICATION
# ============================================================

def classify_swap(
    swap: dict,
) -> Optional[str]:
    """
    Basic Uniswap-V2-style classification.

    A swap where one side enters and the other
    side leaves is detected here.

    Exact BUY/SELL determination requires knowing
    which pair token is the monitored token.
    """

    token0_in = swap["amount0_in"] > 0
    token1_in = swap["amount1_in"] > 0

    token0_out = swap["amount0_out"] > 0
    token1_out = swap["amount1_out"] > 0

    if token0_in and token1_out:
        return "TOKEN0_OUT"

    if token1_in and token0_out:
        return "TOKEN1_OUT"

    return None


# ============================================================
# EVENT PROCESSING
# ============================================================

async def process_swap_log(
    chain: EVMChain,
    log: dict,
    pair_info: dict,
):
    swap = decode_swap_log(
        log
    )

    if not swap:
        return

    direction = classify_swap(
        swap
    )

    if not direction:
        return

    # We intentionally do not yet claim which side
    # is the monitored token.
    #
    # The next token/pair metadata layer will resolve
    # token0/token1 and determine:
    #
    # native/stable -> monitored token = BUY
    # monitored token -> native/stable = SELL

    print(
        "[BuyBot EVM]"
        f" chain={chain.name}"
        f" pair={log.get('address')}"
        f" direction={direction}"
        f" tx={log.get('transactionHash')}"
    )


# ============================================================
# CHAIN SCANNER
# ============================================================

async def scan_chain(
    chain: EVMChain,
):
    last_block = None

    async with httpx.AsyncClient(
        timeout=20
    ) as client:

        while True:

            try:

                current_block = (
                    await get_block_number(
                        client,
                        chain.rpc_url,
                    )
                )

                if last_block is None:

                    last_block = max(
                        0,
                        current_block
                        - BUYBOT_BLOCK_BATCH_SIZE,
                    )

                if current_block <= last_block:

                    await asyncio.sleep(
                        BUYBOT_POLL_SECONDS
                    )

                    continue

                from_block = (
                    last_block + 1
                )

                to_block = min(
                    current_block,
                    from_block
                    + BUYBOT_BLOCK_BATCH_SIZE
                    - 1,
                )

                pairs = (
                    await get_monitored_pairs(
                        chain.name
                    )
                )

                pair_addresses = []

                pair_map = {}

                for row in pairs:

                    pair_address = (
                        row["pair_address"]
                    )

                    if not pair_address:
                        continue

                    normalized = (
                        pair_address.lower()
                    )

                    pair_addresses.append(
                        pair_address
                    )

                    pair_map[
                        normalized
                    ] = dict(row)

                if pair_addresses:

                    logs = await get_logs(
                        client,
                        chain.rpc_url,
                        from_block,
                        to_block,
                        pair_addresses,
                    )

                    for log in logs:

                        pair_address = (
                            log.get(
                                "address"
                            )
                        )

                        pair_info = (
                            pair_map.get(
                                pair_address.lower()
                            )
                            if pair_address
                            else None
                        )

                        if not pair_info:
                            continue

                        await process_swap_log(
                            chain,
                            log,
                            pair_info,
                        )

                last_block = to_block

            except Exception as exc:

                print(
                    "[BuyBot EVM detector error]"
                    f" chain={chain.name}:"
                    f" {exc}"
                )

                await asyncio.sleep(
                    BUYBOT_POLL_SECONDS
                )


# ============================================================
# START ALL EVM CHAINS
# ============================================================

async def evm_detector_worker():
    """
    Run Ethereum, BNB and Robinhood
    detectors concurrently.
    """

    tasks = [
        asyncio.create_task(
            scan_chain(
                CHAINS["ethereum"]
            )
        ),

        asyncio.create_task(
            scan_chain(
                CHAINS["bnb"]
            )
        ),

        asyncio.create_task(
            scan_chain(
                CHAINS["robinhood"]
            )
        ),
    ]

    try:

        await asyncio.gather(
            *tasks
        )

    finally:

        for task in tasks:

            task.cancel()

        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )
