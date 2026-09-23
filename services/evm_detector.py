from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx

from telegram import Bot

from config import (
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

# keccak256(
#   "Swap(address,uint256,uint256,uint256,uint256,address)"
# )
#
# Standard Uniswap V2 Swap event topic0.
#
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
# HEX / DECODING HELPERS
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

        return result.get("result")


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
# FETCH SWAP LOGS
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

    # Swap event has:
    #
    # topic0 = event signature
    # topic1 = sender
    # topic2 = to
    #
    # amount0In
    # amount1In
    # amount0Out
    # amount1Out
    #
    # are inside data.
    #
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
# DETERMINE WHETHER TOKEN WAS BOUGHT
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

    # --------------------------------------------------------
    # TOKEN IS TOKEN0
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # TOKEN IS TOKEN1
    # --------------------------------------------------------

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

    # A normal buy of the monitored token has:
    #
    # quote entering the pool
    # monitored token leaving the pool
    #
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
            SELECT
                setting_value
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

        try:
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

        except Exception as exc:
            print(
                "Failed saving BuyBot event: "
                f"{exc}"
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
# CONVERT QUOTE AMOUNT
# ============================================================

async def calculate_quote_amount(
    rpc: EVMRPC,
    quote_token: str,
    raw_amount: int,
    chain: str,
) -> tuple[Decimal, str]:
    """
    Returns:
        amount
        quote_type

    quote_type can be:
        USD
        NATIVE

    Stablecoins are treated as USD.

    Native/wrapped-native assets are returned as
    native amounts for now. A later market-data
    service will convert them to USD.
    """

    quote_token = normalize_address(
        quote_token
    )

    config = CHAIN_CONFIG.get(
        chain,
        {},
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

    if quote_token in stablecoins:

        # USDT/USDC are normally 6 decimals on
        # the chains supported here.
        #
        # This will be improved later by reading
        # the actual token decimals.
        decimals = await get_token_decimals(
            rpc,
            quote_token,
        )

        amount = (
            Decimal(raw_amount)
            / (
                Decimal(10)
                ** decimals
            )
        )

        return amount, "USD"

    if quote_token in wrapped_native:

        decimals = await get_token_decimals(
            rpc,
            quote_token,
        )

        amount = (
            Decimal(raw_amount)
            / (
                Decimal(10)
                ** decimals
            )
        )

        return amount, "NATIVE"

    # Unknown quote token.
    #
    # We still decode its actual amount but do
    # not pretend that the amount is USD.
    decimals = await get_token_decimals(
        rpc,
        quote_token,
    )

    amount = (
        Decimal(raw_amount)
        / (
            Decimal(10)
            ** decimals
        )
    )

    return amount, "UNKNOWN"


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
    # Get pair tokens
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
    # Determine BUY
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
    # Duplicate protection
    # --------------------------------------------------------

    if await event_exists(
        group_id=group_id,
        chain=chain,
        tx_hash=tx_hash,
        token_address=token_address,
    ):
        return

    # --------------------------------------------------------
    # Token amount
    # --------------------------------------------------------

    decimals = await get_token_decimals(
        rpc,
        token_address,
    )

    received_amount = (
        Decimal(
            trade["token_out"]
        )
        / (
            Decimal(10)
            ** decimals
        )
    )

    # --------------------------------------------------------
    # Quote token
    # --------------------------------------------------------

    if token0 == token_address:
        quote_token = token1
    else:
        quote_token = token0

    quote_amount, quote_type = (
        await calculate_quote_amount(
            rpc=rpc,
            quote_token=quote_token,
            raw_amount=trade[
                "quote_in"
            ],
            chain=chain,
        )
    )

    # --------------------------------------------------------
    # Settings
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Minimum filter
    # --------------------------------------------------------
    #
    # Only apply USD minimum when we actually
    # have a USD-denominated quote.
    #
    # Native assets are NOT falsely treated as USD.
    #

    if (
        minimum_buy > 0
        and quote_type == "USD"
        and quote_amount < minimum_buy
    ):
        return

    # --------------------------------------------------------
    # Store event
    # --------------------------------------------------------

    #
    # Database column is named spent_amount_usd.
    #
    # For stablecoin pairs this is genuinely USD-like.
    #
    # For native/unknown pairs we currently store the
    # decoded quote amount so the event is preserved.
    #
    # The market-data layer will later replace this
    # with a proper USD conversion.
    #

    spent_amount_for_storage = (
        quote_amount
    )

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
            spent_amount_for_storage
        ),
        received_amount=(
            received_amount
        ),
    )

    # --------------------------------------------------------
    # Send shared BuyBot alert
    # --------------------------------------------------------

    try:

        bot = Bot(
            token=__import__(
                "config"
            ).BOT_TOKEN
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
                spent_amount_usd=(
                    quote_amount
                    if quote_type == "USD"
                    else None
                ),
                received_amount=(
                    received_amount
                ),
                tx_hash=tx_hash,
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
        f"received="
        f"{format_decimal(received_amount)} | "
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

        # Start close to the current chain tip
        # on first activation instead of scanning
        # an enormous historical range.
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
    # Cache pair token addresses
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
    # Scan each monitored pair
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
    # Save progress
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
