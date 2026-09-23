from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
from telegram import Bot

from config import (
    BOT_TOKEN,
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    ROBINHOOD_RPC_URL,
    BUYBOT_POLL_SECONDS,
    BUYBOT_BLOCK_BATCH_SIZE,
)

from database.connection import get_pool

from services.buybot_alert import (
    send_buy_alert,
)

from services.market_data import (
    get_market_data_safe,
    quote_amount_to_usd,
)


# ============================================================
# EVM CHAIN CONFIGURATION
# ============================================================

CHAIN_CONFIG = {
    "BNB": {
        "rpc": BNB_RPC_URL,
        "native_symbol": "BNB",
        "wrapped_native": {
            "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        },
        "stablecoins": {
            "0x55d398326f99059ff775485246999027b3197955",
            "0x8ac76a51cc950d9822d68b83fe1ad97b32cd5808",
        },
    },
    "ETH": {
        "rpc": ETHEREUM_RPC_URL,
        "native_symbol": "ETH",
        "wrapped_native": {
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        },
        "stablecoins": {
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "0xdac17f958d2ee523a2206206994597c13d831ec7",
        },
    },
    "ROBINHOOD": {
        "rpc": ROBINHOOD_RPC_URL,
        "native_symbol": "ETH",
        "wrapped_native": set(),
        "stablecoins": set(),
    },
}


# ============================================================
# UNISWAP V2 / PANCAKESWAP V2 SWAP EVENT
# ============================================================

SWAP_EVENT_TOPIC = (
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657"
    "fb8d5e3d130840159d822"
)


# ============================================================
# ERC20 FUNCTION SELECTORS
# ============================================================

DECIMALS_SELECTOR = "0x313ce567"

TOKEN0_SELECTOR = "0x0dfe1681"

TOKEN1_SELECTOR = "0xd21220a7"


# ============================================================
# ADDRESS HELPERS
# ============================================================

def normalize_address(
    address: str | None,
) -> str | None:

    if not address:
        return None

    return str(address).strip().lower()


def topic_address(
    topic: str,
) -> str:

    value = str(topic).lower()

    if value.startswith("0x"):
        value = value[2:]

    return "0x" + value[-40:]


# ============================================================
# UINT256 DECODER
# ============================================================

def decode_uint256(
    data: str,
    index: int,
) -> int:

    if not data:
        return 0

    raw = (
        data[2:]
        if data.startswith("0x")
        else data
    )

    start = index * 64

    end = start + 64

    if len(raw) < end:
        return 0

    return int(
        raw[start:end],
        16,
    )


def format_decimal(
    value: Decimal,
    places: int = 6,
) -> str:

    text = f"{value:.{places}f}"

    text = (
        text
        .rstrip("0")
        .rstrip(".")
    )

    return text or "0"


# ============================================================
# RPC CLIENT
# ============================================================

class EVMRPC:

    def __init__(
        self,
        rpc_url: str,
    ):
        self.rpc_url = rpc_url
        self.request_id = 0

    async def call(
        self,
        method: str,
        params: list,
    ):

        self.request_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params,
        }

        timeout = httpx.Timeout(
            20.0,
            connect=10.0,
        )

        async with httpx.AsyncClient(
            timeout=timeout,
        ) as client:

            response = await client.post(
                self.rpc_url,
                json=payload,
            )

            response.raise_for_status()

            result = response.json()

        if "error" in result:

            raise RuntimeError(
                f"RPC error: {result['error']}"
            )

        return result.get(
            "result"
        )


# ============================================================
# ERC20 DECIMALS
# ============================================================

async def get_token_decimals(
    rpc: EVMRPC,
    token_address: str,
) -> int:

    try:

        result = await rpc.call(
            "eth_call",
            [
                {
                    "to": token_address,
                    "data": DECIMALS_SELECTOR,
                },
                "latest",
            ],
        )

        if not result:
            return 18

        decimals = int(
            result,
            16,
        )

        if decimals < 0 or decimals > 36:
            return 18

        return decimals

    except Exception:
        return 18


# ============================================================
# PAIR TOKEN0 / TOKEN1
# ============================================================

async def get_pair_token(
    rpc: EVMRPC,
    pair_address: str,
    selector: str,
) -> str | None:

    try:

        result = await rpc.call(
            "eth_call",
            [
                {
                    "to": pair_address,
                    "data": selector,
                },
                "latest",
            ],
        )

        if not result:
            return None

        return (
            "0x"
            + result[-40:].lower()
        )

    except Exception:
        return None


# ============================================================
# CURRENT BLOCK
# ============================================================

async def get_latest_block(
    rpc: EVMRPC,
) -> int:

    result = await rpc.call(
        "eth_blockNumber",
        [],
    )

    return int(
        result,
        16,
    )


# ============================================================
# SWAP LOGS
# ============================================================

async def get_swap_logs(
    rpc: EVMRPC,
    pair_address: str,
    from_block: int,
    to_block: int,
) -> list:

    try:

        result = await rpc.call(
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(
                        from_block
                    ),
                    "toBlock": hex(
                        to_block
                    ),
                    "address": pair_address,
                    "topics": [
                        SWAP_EVENT_TOPIC
                    ],
                }
            ],
        )

        return result or []

    except Exception as exc:

        print(
            "EVM log query failed for "
            f"{pair_address}: {exc}"
        )

        return []


# ============================================================
# SWAP DECODER
# ============================================================

def decode_swap_log(
    log: dict,
) -> dict | None:

    topics = log.get(
        "topics",
        [],
    )

    data = log.get(
        "data",
        "0x",
    )

    if len(topics) < 3:
        return None

    try:

        sender = topic_address(
            topics[1]
        )

        buyer = topic_address(
            topics[2]
        )

        amount0_in = decode_uint256(
            data,
            0,
        )

        amount1_in = decode_uint256(
            data,
            1,
        )

        amount0_out = decode_uint256(
            data,
            2,
        )

        amount1_out = decode_uint256(
            data,
            3,
        )

        return {
            "sender": sender,
            "buyer": buyer,
            "amount0_in": amount0_in,
            "amount1_in": amount1_in,
            "amount0_out": amount0_out,
            "amount1_out": amount1_out,
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
            "log_index": int(
                log.get(
                    "logIndex",
                    "0x0",
                ),
                16,
            ),
        }

    except Exception as exc:

        print(
            "Failed to decode EVM swap: "
            f"{exc}"
        )

        return None


# ============================================================
# DETERMINE BUY
# ============================================================

def determine_trade(
    swap: dict,
    token_address: str,
    token0: str,
    token1: str,
) -> dict | None:

    token_address = normalize_address(
        token_address
    )

    token0 = normalize_address(
        token0
    )

    token1 = normalize_address(
        token1
    )

    if not token_address:
        return None

    if not token0 or not token1:
        return None

    if token_address == token0:

        token_in = swap[
            "amount0_in"
        ]

        token_out = swap[
            "amount0_out"
        ]

        quote_in = swap[
            "amount1_in"
        ]

        quote_out = swap[
            "amount1_out"
        ]

        token_side = "token0"

    elif token_address == token1:

        token_in = swap[
            "amount1_in"
        ]

        token_out = swap[
            "amount1_out"
        ]

        quote_in = swap[
            "amount0_in"
        ]

        quote_out = swap[
            "amount0_out"
        ]

        token_side = "token1"

    else:
        return None

    if (
        token_out > 0
        and quote_in > 0
    ):

        return {
            "type": "BUY",
            "token_in": token_in,
            "token_out": token_out,
            "quote_in": quote_in,
            "quote_out": quote_out,
            "token_side": token_side,
        }

    return None


# ============================================================
# DATABASE HELPERS
# ============================================================

async def get_monitored_tokens():

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
              AND pair_address IS NOT NULL
              AND pair_address <> ''
              AND chain IN (
                  'BNB',
                  'ETH',
                  'ROBINHOOD'
              )
            ORDER BY id ASC
            """
        )

    return rows


async def get_last_block(
    chain: str,
) -> int | None:

    pool = await get_pool()

    key = (
        "buybot_last_block_"
        f"{chain.lower()}"
    )

    async with pool.acquire() as connection:

        row = await connection.fetchrow(
            """
            SELECT setting_value
            FROM system_settings
            WHERE setting_key = $1
            """,
            key,
        )

    if not row:
        return None

    try:
        return int(
            row["setting_value"]
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


async def save_last_block(
    chain: str,
    block_number: int,
):

    pool = await get_pool()

    key = (
        "buybot_last_block_"
        f"{chain.lower()}"
    )

    async with pool.acquire() as connection:

        await connection.execute(
            """
            INSERT INTO system_settings (
                setting_key,
                setting_value,
                description
            )
            VALUES (
                $1,
                $2,
                $3
            )
            ON CONFLICT (
                setting_key
            )
            DO UPDATE SET
                setting_value =
                    EXCLUDED.setting_value,
                updated_at = NOW()
            """,
            key,
            str(block_number),
            "BuyBot EVM detector last processed block.",
        )


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


# ============================================================
# BUYBOT SETTINGS
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
                min_buy_usd
            FROM buybot_settings
            WHERE group_id = $1
            """,
            group_id,
        )


# ============================================================
# QUOTE DECIMALS
# ============================================================

async def get_quote_amount(
    rpc: EVMRPC,
    quote_token: str,
    raw_amount: int,
) -> Decimal:

    decimals = await get_token_decimals(
        rpc,
        quote_token,
    )

    return (
        Decimal(raw_amount)
        / (
            Decimal(10)
            ** decimals
        )
    )


# ============================================================
# QUOTE TYPE
# ============================================================

def get_quote_type(
    chain: str,
    quote_token: str,
) -> str:

    config = CHAIN_CONFIG.get(
        chain,
        {},
    )

    normalized = normalize_address(
        quote_token
    )

    stablecoins = {
        normalize_address(address)
        for address in config.get(
            "stablecoins",
            set(),
        )
    }

    wrapped_native = {
        normalize_address(address)
        for address in config.get(
            "wrapped_native",
            set(),
        )
    }

    if normalized in stablecoins:
        return "USD"

    if normalized in wrapped_native:
        return "NATIVE"

    return "UNKNOWN"


# ============================================================
# PROCESS ONE SWAP
# ============================================================

async def process_swap(
    chain: str,
    token,
    rpc: EVMRPC,
    swap: dict,
    pair_cache: dict,
):

    token_address = normalize_address(
        token["contract_address"]
    )

    pair_address = normalize_address(
        token["pair_address"]
    )

    if not token_address:
        return

    if not pair_address:
        return

    # --------------------------------------------------------
    # PAIR TOKENS
    # --------------------------------------------------------

    if pair_address not in pair_cache:

        token0 = await get_pair_token(
            rpc,
            pair_address,
            TOKEN0_SELECTOR,
        )

        token1 = await get_pair_token(
            rpc,
            pair_address,
            TOKEN1_SELECTOR,
        )

        pair_cache[
            pair_address
        ] = (
            token0,
            token1,
        )

    token0, token1 = pair_cache[
        pair_address
    ]

    if not token0 or not token1:
        return

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    trade = determine_trade(
        swap=swap,
        token_address=token_address,
        token0=token0,
        token1=token1,
    )

    if not trade:
        return

    tx_hash = swap.get(
        "tx_hash"
    )

    if not tx_hash:
        return

    group_id = int(
        token["group_id"]
    )

    # --------------------------------------------------------
    # DUPLICATE
    # --------------------------------------------------------

    if await event_exists(
        group_id=group_id,
        chain=chain,
        tx_hash=tx_hash,
        token_address=token_address,
    ):
        return

    # --------------------------------------------------------
    # TOKEN AMOUNT
    # --------------------------------------------------------

    token_decimals = (
        await get_token_decimals(
            rpc,
            token_address,
        )
    )

    received_amount = (
        Decimal(
            trade["token_out"]
        )
        / (
            Decimal(10)
            ** token_decimals
        )
    )

    # --------------------------------------------------------
    # QUOTE TOKEN
    # --------------------------------------------------------

    if token0 == token_address:
        quote_token = token1
    else:
        quote_token = token0

    quote_amount = (
        await get_quote_amount(
            rpc=rpc,
            quote_token=quote_token,
            raw_amount=trade[
                "quote_in"
            ],
        )
    )

    quote_type = get_quote_type(
        chain=chain,
        quote_token=quote_token,
    )

    # --------------------------------------------------------
    # CONVERT QUOTE TO USD
    # --------------------------------------------------------

    spent_usd = (
        await quote_amount_to_usd(
            chain=chain,
            quote_type=quote_type,
            amount=quote_amount,
        )
    )

    # --------------------------------------------------------
    # MARKET DATA
    # --------------------------------------------------------

    market_data = (
        await get_market_data_safe(
            chain=chain,
            token_address=token_address,
        )
    )

    market_cap_usd = (
        market_data.get(
            "market_cap_usd"
        )
    )

    dex_url = (
        token["dex_url"]
        or market_data.get(
            "dex_url"
        )
    )

    # --------------------------------------------------------
    # SETTINGS
    # --------------------------------------------------------

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
            settings[
                "min_buy_usd"
            ]
            or 0
        )
    )

    # --------------------------------------------------------
    # MINIMUM BUY FILTER
    # --------------------------------------------------------

    if (
        minimum_buy > 0
        and spent_usd is not None
        and spent_usd < minimum_buy
    ):
        return

    # If USD price isn't available yet, we don't
    # reject the buy. This prevents the detector
    # from silently losing real transactions.
    #
    # Once the market-price layer returns a value,
    # the configured minimum is enforced.

    # --------------------------------------------------------
    # SAVE EVENT
    # --------------------------------------------------------

    await save_event(
        group_id=group_id,
        chain=chain,
        tx_hash=tx_hash,
        token_address=token_address,
        token_symbol=token[
            "token_symbol"
        ],
        buyer_address=swap.get(
            "buyer"
        ),
        spent_amount_usd=(
            spent_usd
            if spent_usd is not None
            else Decimal("0")
        ),
        received_amount=(
            received_amount
        ),
    )

    # --------------------------------------------------------
    # SEND ALERT
    # --------------------------------------------------------

    try:

        bot = Bot(
            token=BOT_TOKEN
        )

        try:

            await send_buy_alert(
                bot=bot,
                group_id=group_id,
                chain=chain,
                token_address=token_address,
                token_name=token[
                    "token_name"
                ],
                token_symbol=token[
                    "token_symbol"
                ],
                buyer_address=swap.get(
                    "buyer"
                ),
                spent_amount_usd=spent_usd,
                received_amount=received_amount,
                tx_hash=tx_hash,
                market_cap_usd=market_cap_usd,
                dex_url=dex_url,
                trending_url=None,
            )

        finally:

            await bot.shutdown()

    except Exception as exc:

        print(
            "Failed sending EVM "
            "BuyBot alert: "
            f"{exc}"
        )

    print(
        "EVM BUY DETECTED | "
        f"chain={chain} | "
        f"group={group_id} | "
        f"token={token['token_symbol']} | "
        f"quote={format_decimal(quote_amount)} "
        f"{quote_type} | "
        f"spent_usd={spent_usd} | "
        f"received="
        f"{format_decimal(received_amount)} | "
        f"market_cap={market_cap_usd} | "
        f"buyer={swap.get('buyer')} | "
        f"tx={tx_hash}"
    )


# ============================================================
# PROCESS CHAIN
# ============================================================

async def process_chain(
    chain: str,
):

    config = CHAIN_CONFIG.get(
        chain
    )

    if not config:
        return

    rpc_url = config.get(
        "rpc"
    )

    if not rpc_url:

        print(
            f"No RPC configured for {chain}."
        )

        return

    rpc = EVMRPC(
        rpc_url
    )

    tokens = (
        await get_monitored_tokens()
    )

    chain_tokens = [
        token
        for token in tokens
        if token["chain"] == chain
    ]

    if not chain_tokens:
        return

    latest_block = (
        await get_latest_block(
            rpc
        )
    )

    last_block = (
        await get_last_block(
            chain
        )
    )

    if last_block is None:

        last_block = max(
            0,
            latest_block - 2,
        )

    if last_block >= latest_block:
        return

    start_block = (
        last_block + 1
    )

    end_block = min(
        latest_block,
        start_block
        + BUYBOT_BLOCK_BATCH_SIZE
        - 1,
    )

    print(
        f"BuyBot {chain}: "
        f"scanning blocks "
        f"{start_block}-"
        f"{end_block}"
    )

    pair_cache = {}

    # --------------------------------------------------------
    # PAIR CACHE
    # --------------------------------------------------------

    for token in chain_tokens:

        pair_address = (
            normalize_address(
                token[
                    "pair_address"
                ]
            )
        )

        if not pair_address:
            continue

        if pair_address in pair_cache:
            continue

        token0 = await get_pair_token(
            rpc,
            pair_address,
            TOKEN0_SELECTOR,
        )

        token1 = await get_pair_token(
            rpc,
            pair_address,
            TOKEN1_SELECTOR,
        )

        pair_cache[
            pair_address
        ] = (
            token0,
            token1,
        )

    # --------------------------------------------------------
    # SCAN LOGS
    # --------------------------------------------------------

    for token in chain_tokens:

        pair_address = (
            normalize_address(
                token[
                    "pair_address"
                ]
            )
        )

        if not pair_address:
            continue

        logs = await get_swap_logs(
            rpc=rpc,
            pair_address=pair_address,
            from_block=start_block,
            to_block=end_block,
        )

        if not logs:
            continue

        for log in logs:

            swap = decode_swap_log(
                log
            )

            if not swap:
                continue

            try:

                await process_swap(
                    chain=chain,
                    token=token,
                    rpc=rpc,
                    swap=swap,
                    pair_cache=pair_cache,
                )

            except Exception as exc:

                print(
                    "BuyBot EVM swap "
                    "processing error: "
                    f"{exc}"
                )

    # --------------------------------------------------------
    # SAVE PROGRESS
    # --------------------------------------------------------

    await save_last_block(
        chain,
        end_block,
    )


# ============================================================
# CHAIN LOOP
# ============================================================

async def evm_chain_loop(
    chain: str,
):

    print(
        f"{chain} BuyBot detector "
        "started."
    )

    while True:

        try:

            await process_chain(
                chain
            )

        except asyncio.CancelledError:

            print(
                f"{chain} BuyBot detector "
                "stopped."
            )

            raise

        except Exception as exc:

            print(
                f"{chain} BuyBot detector "
                f"error: {exc}"
            )

        await asyncio.sleep(
            BUYBOT_POLL_SECONDS
        )


# ============================================================
# MAIN EVM WORKER
# ============================================================

async def evm_detector_worker():

    print(
        "EVM BuyBot detector worker "
        "started."
    )

    tasks = [
        asyncio.create_task(
            evm_chain_loop(
                "BNB"
            )
        ),
        asyncio.create_task(
            evm_chain_loop(
                "ETH"
            )
        ),
        asyncio.create_task(
            evm_chain_loop(
                "ROBINHOOD"
            )
        ),
    ]

    try:

        await asyncio.gather(
            *tasks
        )

    except asyncio.CancelledError:

        for task in tasks:

            if not task.done():
                task.cancel()

        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        raise
