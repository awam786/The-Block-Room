import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

import httpx

from config import (
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    ROBINHOOD_RPC_URL,
    BUYBOT_BLOCK_BATCH_SIZE,
    BUYBOT_POLL_SECONDS,
)

from database.connection import get_pool

from services.buybot_events import BuyEvent

from services.buybot_dispatcher import (
    process_buy_event,
)

from services.evm_metadata import (
    PairMetadata,
    get_pair_metadata,
    get_swap_amounts,
)


# Standard Uniswap V2-style:
# Swap(
#     address indexed sender,
#     uint256 amount0In,
#     uint256 amount1In,
#     uint256 amount0Out,
#     uint256 amount1Out,
#     address indexed to
# )
SWAP_EVENT_TOPIC = (
    "0xd78ad95fa46c994b6551d0da85fc275"
    "fe613ce37657fb8d5e3d130840159d822"
)


@dataclass
class EVMChain:
    name: str
    chain_id: int
    rpc_url: str


EVM_CHAINS = [
    EVMChain(
        name="ethereum",
        chain_id=1,
        rpc_url=ETHEREUM_RPC_URL,
    ),
    EVMChain(
        name="bnb",
        chain_id=56,
        rpc_url=BNB_RPC_URL,
    ),
    EVMChain(
        name="robinhood",
        chain_id=4663,
        rpc_url=ROBINHOOD_RPC_URL,
    ),
]


_pair_cache: Dict[str, PairMetadata] = {}


async def rpc_call(
    rpc_url: str,
    method: str,
    params: list,
):
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
            rpc_url,
            json=payload,
        )

        response.raise_for_status()

        data = response.json()

    if data.get("error"):
        raise RuntimeError(
            data["error"].get(
                "message",
                "RPC request failed",
            )
        )

    return data.get("result")


async def get_block_number(
    rpc_url: str,
) -> int:
    result = await rpc_call(
        rpc_url,
        "eth_blockNumber",
        [],
    )

    return int(result, 16)


async def get_logs(
    rpc_url: str,
    from_block: int,
    to_block: int,
    pair_addresses: List[str],
):
    if not pair_addresses:
        return []

    logs = await rpc_call(
        rpc_url,
        "eth_getLogs",
        [
            {
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "address": pair_addresses,
                "topics": [
                    SWAP_EVENT_TOPIC,
                ],
            }
        ],
    )

    return logs or []


def decode_uint256(
    value: str,
) -> int:
    if not value:
        return 0

    return int(value, 16)


def decode_swap_log(
    log: dict,
):
    data = log.get("data")

    if not data or data == "0x":
        return None

    raw = data[2:]

    # Four uint256 values = 128 bytes = 256 hex characters.
    if len(raw) < 256:
        return None

    try:
        amount0_in = decode_uint256(
            raw[0:64]
        )

        amount1_in = decode_uint256(
            raw[64:128]
        )

        amount0_out = decode_uint256(
            raw[128:192]
        )

        amount1_out = decode_uint256(
            raw[192:256]
        )

    except ValueError:
        return None

    topics = log.get("topics") or []

    sender = ""

    buyer_or_recipient = ""

    if len(topics) >= 2:
        sender = (
            "0x"
            + topics[1][-40:]
        )

    if len(topics) >= 3:
        buyer_or_recipient = (
            "0x"
            + topics[2][-40:]
        )

    return {
        "pair_address": (
            log.get("address") or ""
        ).lower(),
        "sender": sender,
        "recipient": buyer_or_recipient,
        "amount0_in": amount0_in,
        "amount1_in": amount1_in,
        "amount0_out": amount0_out,
        "amount1_out": amount1_out,
        "block_number": int(
            log.get("blockNumber", "0x0"),
            16,
        ),
        "tx_hash": log.get(
            "transactionHash"
        ),
        "log_index": int(
            log.get("logIndex", "0x0"),
            16,
        ),
    }


async def get_monitored_pairs(
    chain: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                group_id,
                contract_address,
                pair_address,
                token_name,
                token_symbol
            FROM buybot_tokens
            WHERE LOWER(chain) = LOWER($1)
              AND enabled = TRUE
              AND pair_address IS NOT NULL
              AND pair_address <> '';
            """,
            chain,
        )

    return rows


async def get_pair_cache(
    rpc_url: str,
    pair_address: str,
) -> Optional[PairMetadata]:
    key = pair_address.lower()

    if key in _pair_cache:
        return _pair_cache[key]

    pair = await get_pair_metadata(
        rpc_url,
        pair_address,
    )

    if pair:
        _pair_cache[key] = pair

    return pair


def choose_quote_amount(
    swap_data: dict,
    decoded_amounts: dict,
) -> Decimal:
    """
    Select the amount entering the pool as the quote side.

    For a BUY, the quote asset is normally the input.
    """

    if not decoded_amounts["is_buy"]:
        return Decimal("0")

    return decoded_amounts["quote_in"]


def is_probable_stablecoin(
    symbol: str,
) -> bool:
    return symbol.upper() in {
        "USDT",
        "USDC",
        "DAI",
        "FDUSD",
        "BUSD",
    }


def estimate_usd_value(
    quote_amount: Decimal,
    quote_symbol: str,
) -> Decimal:
    """
    Stablecoin pairs can be valued directly in USD.

    Native-asset pairs are handled later by the
    price layer. For now they return zero rather
    than inventing a USD price.
    """

    if quote_amount <= 0:
        return Decimal("0")

    if is_probable_stablecoin(
        quote_symbol
    ):
        return quote_amount

    return Decimal("0")


async def build_buy_event(
    chain: EVMChain,
    monitored_token: dict,
    swap_data: dict,
):
    pair_address = swap_data["pair_address"]

    pair = await get_pair_cache(
        chain.rpc_url,
        pair_address,
    )

    if not pair:
        return None

    decoded = get_swap_amounts(
        pair=pair,
        monitored_address=monitored_token[
            "contract_address"
        ],
        amount0_in=swap_data[
            "amount0_in"
        ],
        amount1_in=swap_data[
            "amount1_in"
        ],
        amount0_out=swap_data[
            "amount0_out"
        ],
        amount1_out=swap_data[
            "amount1_out"
        ],
    )

    if not decoded:
        return None

    if not decoded["is_buy"]:
        return None

    quote_amount = choose_quote_amount(
        swap_data,
        decoded,
    )

    quote_symbol = decoded[
        "quote_symbol"
    ]

    spent_usd = estimate_usd_value(
        quote_amount,
        quote_symbol,
    )

    monitored_address = (
        monitored_token[
            "contract_address"
        ]
    ).lower()

    if pair.token0.address.lower() == monitored_address:
        received_symbol = pair.token0.symbol
    elif pair.token1.address.lower() == monitored_address:
        received_symbol = pair.token1.symbol
    else:
        return None

    return BuyEvent(
        group_id=monitored_token[
            "group_id"
        ],
        chain=chain.name,
        tx_hash=swap_data[
            "tx_hash"
        ],
        token_address=monitored_token[
            "contract_address"
        ],
        token_name=(
            monitored_token.get(
                "token_name"
            )
            or (
                pair.token0.name
                if pair.token0.address.lower()
                == monitored_address
                else pair.token1.name
            )
        ),
        token_symbol=(
            monitored_token.get(
                "token_symbol"
            )
            or received_symbol
        ),
        buyer_address=swap_data[
            "recipient"
        ] or swap_data[
            "sender"
        ],
        spent_amount_usd=spent_usd,
        spent_native_amount=quote_amount,
        spent_native_symbol=quote_symbol,
        received_amount=decoded[
            "token_received"
        ],
        received_symbol=received_symbol,
        market_cap_usd=Decimal("0"),
        is_new_holder=False,
        dex_url=None,
        buy_url=None,
        trending_url=None,
        block_number=swap_data[
            "block_number"
        ],
    )


async def process_chain_logs(
    chain: EVMChain,
    logs: list,
    monitored_pairs: dict,
):
    processed = 0

    for log in logs:
        swap_data = decode_swap_log(
            log
        )

        if not swap_data:
            continue

        pair_address = swap_data[
            "pair_address"
        ].lower()

        tokens = monitored_pairs.get(
            pair_address
        )

        if not tokens:
            continue

        for monitored_token in tokens:
            try:
                event = await build_buy_event(
                    chain,
                    monitored_token,
                    swap_data,
                )

                if not event:
                    continue

                sent = await process_buy_event(
                    event
                )

                processed += sent

            except Exception as exc:
                print(
                    "Buy event processing error "
                    f"chain={chain.name} "
                    f"tx={swap_data.get('tx_hash')} "
                    f"pair={pair_address}: "
                    f"{exc}"
                )

    return processed


async def scan_chain(
    chain: EVMChain,
):
    last_block = None

    print(
        f"BuyBot EVM detector started: "
        f"{chain.name} "
        f"(chain {chain.chain_id})"
    )

    while True:
        try:
            current_block = await get_block_number(
                chain.rpc_url
            )

            if last_block is None:
                last_block = current_block - 1

            if current_block <= last_block:
                await asyncio.sleep(
                    BUYBOT_POLL_SECONDS
                )
                continue

            from_block = last_block + 1

            to_block = min(
                current_block,
                from_block
                + BUYBOT_BLOCK_BATCH_SIZE
                - 1,
            )

            rows = await get_monitored_pairs(
                chain.name
            )

            monitored_pairs = {}

            for row in rows:
                pair_address = (
                    row["pair_address"]
                    or ""
                ).lower()

                if not pair_address:
                    continue

                monitored_pairs.setdefault(
                    pair_address,
                    [],
                ).append(
                    {
                        "group_id": row[
                            "group_id"
                        ],
                        "contract_address": row[
                            "contract_address"
                        ],
                        "token_name": row[
                            "token_name"
                        ],
                        "token_symbol": row[
                            "token_symbol"
                        ],
                    }
                )

            pair_addresses = list(
                monitored_pairs.keys()
            )

            if pair_addresses:
                logs = await get_logs(
                    chain.rpc_url,
                    from_block,
                    to_block,
                    pair_addresses,
                )

                if logs:
                    await process_chain_logs(
                        chain,
                        logs,
                        monitored_pairs,
                    )

            last_block = to_block

            await asyncio.sleep(
                BUYBOT_POLL_SECONDS
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                f"BuyBot detector error "
                f"chain={chain.name}: "
                f"{exc}"
            )

            await asyncio.sleep(
                BUYBOT_POLL_SECONDS
            )


async def evm_detector_worker():
    tasks = [
        asyncio.create_task(
            scan_chain(chain)
        )
        for chain in EVM_CHAINS
    ]

    print(
        "EVM BuyBot detector worker launched."
    )

    try:
        await asyncio.gather(
            *tasks
        )

    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()

        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        raise
