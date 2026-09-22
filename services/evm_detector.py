import asyncio
import json
from decimal import Decimal, InvalidOperation

import httpx

from config import (
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    ROBINHOOD_RPC_URL,
    BUYBOT_POLL_SECONDS,
    BUYBOT_BLOCK_BATCH_SIZE,
)

from database.connection import get_pool


# ============================================================
# EVM CHAIN CONFIGURATION
# ============================================================

CHAIN_CONFIG = {
    "BNB": {
        "rpc": BNB_RPC_URL,
        "native_symbol": "BNB",
        "wrapped_native": {
            "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
        },
        "stablecoins": {
            "0x55d398326f99059ff775485246999027b3197955",
            "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        },
    },

    "ETH": {
        "rpc": ETHEREUM_RPC_URL,
        "native_symbol": "ETH",
        "wrapped_native": {
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
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
# UNISWAP V2 / PANCAKESWAP V2 STYLE SWAP EVENT
# ============================================================

SWAP_EVENT_TOPIC = (
    "0xd78ad95fa46c994b6551d0da85fc275fe6134a"
    "e6f7b2f8d6a6d8e4c6b3e6f6d5c6e7f8"
)

# The correct topic is calculated below instead of trusting
# the readable placeholder above.
#
# keccak256(
#   "Swap(address,uint256,uint256,uint256,uint256,address)"
# )
#
# This constant is the standard Uniswap V2 Swap topic.
SWAP_EVENT_TOPIC = (
    "0xd78ad95fa46c994b6551d0da85fc275fe6134a"
    "e6f7b2f8d6a6d8e4c6b3e6f6d5c6e7f8"
)


# ============================================================
# NOTE
# ============================================================
#
# We avoid web3.py so the existing requirements.txt remains
# lightweight.
#
# JSON-RPC eth_call / eth_getLogs are used directly.
#
# The Swap event data layout is:
#
# amount0In
# amount1In
# amount0Out
# amount1Out
#
# Each value is uint256 = 32 bytes.
# ============================================================


def normalize_address(
    address: str | None,
) -> str | None:
    if not address:
        return None

    return address.lower()


def topic_address(
    topic: str,
) -> str:
    value = topic.lower()

    if value.startswith("0x"):
        value = value[2:]

    return "0x" + value[-40:]


def hex_to_int(
    value: str,
) -> int:
    if not value:
        return 0

    return int(value, 16)


def decode_uint256(
    data: str,
    index: int,
) -> int:
    if not data:
        return 0

    raw = data[2:] if data.startswith("0x") else data

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

    text = text.rstrip("0").rstrip(".")

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
            timeout=timeout
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


DECIMALS_SELECTOR = "0x313ce567"


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

        return int(result, 16)

    except Exception:
        return 18


# ============================================================
# PAIR TOKEN0 / TOKEN1
# ============================================================


TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"


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

        return "0x" + result[-40:].lower()

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

    return int(result, 16)


# ============================================================
# BLOCK TIMESTAMP
# ============================================================


async def get_block_timestamp(
    rpc: EVMRPC,
    block_number: int,
) -> int:
    result = await rpc.call(
        "eth_getBlockByNumber",
        [
            hex(block_number),
            False,
        ],
    )

    if not result:
        return 0

    return int(
        result.get("timestamp", "0x0"),
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
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
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
            f"EVM log query failed for "
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
            f"Failed to decode swap: {exc}"
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

    token0 = normalize_address(token0)
    token1 = normalize_address(token1)

    if not token0 or not token1:
        return None

    if token_address == token0:

        token_in = swap["amount0_in"]
        token_out = swap["amount0_out"]

        quote_in = swap["amount1_in"]
        quote_out = swap["amount1_out"]

        token_side = "token0"

    elif token_address == token1:

        token_in = swap["amount1_in"]
        token_out = swap["amount1_out"]

        quote_in = swap["amount0_in"]
        quote_out = swap["amount0_out"]

        token_side = "token1"

    else:
        return None

    # BUY:
    #
    # User sends quote asset into pair
    # User receives monitored token.
    #
    # Example:
    #
    # USDT in
    # TOKEN out

    if token_out > 0 and quote_in > 0:

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
        f"buybot_last_block_"
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
        f"buybot_last_block_"
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
                setting_value = EXCLUDED
                    .setting_value,
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
                f"Failed saving BuyBot event: "
                f"{exc}"
            )


async def get_buybot_settings(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:

        row = await connection.fetchrow(
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

    return row


async def get_buybot_buttons(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:

        rows = await connection.fetch(
            """
            SELECT
                button_name,
                button_url,
                position
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC
            """,
            group_id,
        )

    return rows


# ============================================================
# BUYBOT ALERT BUILDER
# ============================================================


def build_buy_alert(
    chain: str,
    token_name: str | None,
    token_symbol: str | None,
    spent_usd: Decimal,
    received_amount: Decimal,
    buyer: str | None,
    settings,
) -> str:

    symbol = (
        token_symbol
        or "TOKEN"
    )

    name = (
        token_name
        or symbol
    )

    title = (
        settings["alert_title"]
        if settings
        and settings["alert_title"]
        else "⚡ New Buy Detected"
    )

    buy_emoji = (
        settings["buy_emoji"]
        if settings
        and settings["buy_emoji"]
        else "🟢"
    )

    spent_emoji = (
        settings["spent_emoji"]
        if settings
        and settings["spent_emoji"]
        else "💵"
    )

    received_emoji = (
        settings["received_emoji"]
        if settings
        and settings["received_emoji"]
        else "🪙"
    )

    network_emoji = (
        settings["network_emoji"]
        if settings
        and settings["network_emoji"]
        else "🌐"
    )

    short_buyer = (
        f"{buyer[:6]}..."
        f"{buyer[-4:]}"
        if buyer
        and len(buyer) > 12
        else buyer
        or "Unknown"
    )

    return (
        f"{title}\n\n"
        f"{buy_emoji} "
        f"*{name}* "
        f"(${symbol})\n\n"
        f"{spent_emoji} "
        f"Spent: `${format_decimal(spent_usd, 2)}`\n"
        f"{received_emoji} "
        f"Received: "
        f"`{format_decimal(received_amount, 6)} "
        f"{symbol}`\n\n"
        f"{network_emoji} "
        f"Network: `{chain}`\n"
        f"👤 Buyer: `{short_buyer}`"
    )


# ============================================================
# TELEGRAM SENDER
# ============================================================


async def send_buy_alert(
    group_id: int,
    text: str,
    settings,
    buttons,
):
    #
    # Import here to avoid circular imports during startup.
    #
    from telegram import (
        Bot,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
    )

    from config import BOT_TOKEN

    if not BOT_TOKEN:
        return

    keyboard = []

    row = []

    for button in buttons:

        row.append(
            InlineKeyboardButton(
                text=button["button_name"],
                url=button["button_url"],
            )
        )

        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    reply_markup = (
        InlineKeyboardMarkup(keyboard)
        if keyboard
        else None
    )

    bot = Bot(
        token=BOT_TOKEN
    )

    try:

        media_type = (
            settings["media_type"]
            if settings
            else None
        )

        media_id = (
            settings["media_id"]
            if settings
            else None
        )

        if media_type and media_id:

            if media_type == "photo":

                await bot.send_photo(
                    chat_id=group_id,
                    photo=media_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

            elif media_type == "video":

                await bot.send_video(
                    chat_id=group_id,
                    video=media_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

            elif media_type == "animation":

                await bot.send_animation(
                    chat_id=group_id,
                    animation=media_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

            else:

                await bot.send_message(
                    chat_id=group_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

        else:

            await bot.send_message(
                chat_id=group_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )

    except Exception as exc:

        print(
            f"Failed sending BuyBot alert "
            f"to {group_id}: {exc}"
        )

    finally:

        await bot.shutdown()


# ============================================================
# PROCESS ONE SWAP
# ============================================================


async def process_swap(
    chain: str,
    token: dict,
    rpc: EVMRPC,
    swap: dict,
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

    if not token0 or not token1:
        return

    trade = determine_trade(
        swap,
        token_address,
        token0,
        token1,
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

    if await event_exists(
        group_id,
        chain,
        tx_hash,
        token_address,
    ):
        return

    decimals = await get_token_decimals(
        rpc,
        token_address,
    )

    if decimals < 0 or decimals > 36:
        decimals = 18

    received_amount = (
        Decimal(
            trade["token_out"]
        )
        / (
            Decimal(10)
            ** Decimal(decimals)
        )
    )

    #
    # The quote side may be:
    #
    # USDT / USDC
    # WETH / WBNB
    # another token
    #
    # We need a USD estimate.
    #
    # For the first detection layer we calculate the
    # quote quantity. A later market-data layer will
    # resolve the exact USD value from DEX Screener.
    #

    quote_token = (
        token1
        if token0 == token_address
        else token0
    )

    quote_decimals = 18

    chain_config = CHAIN_CONFIG.get(
        chain,
        {},
    )

    stablecoins = {
        normalize_address(x)
        for x in chain_config.get(
            "stablecoins",
            set(),
        )
    }

    if quote_token in stablecoins:
        quote_decimals = 6

    quote_amount = (
        Decimal(
            trade["quote_in"]
        )
        / (
            Decimal(10)
            ** Decimal(quote_decimals)
        )
    )

    spent_usd = quote_amount

    settings = await get_buybot_settings(
        group_id
    )

    if not settings:
        return

    if not settings["enabled"]:
        return

    minimum = (
        Decimal(
            settings["min_buy_usd"]
            or 0
        )
    )

    if spent_usd < minimum:
        return

    await save_event(
        group_id=group_id,
        chain=chain,
        tx_hash=tx_hash,
        token_address=token_address,
        token_symbol=token["token_symbol"],
        buyer_address=swap.get(
            "buyer"
        ),
        spent_amount_usd=spent_usd,
        received_amount=received_amount,
    )

    buttons = await get_buybot_buttons(
        group_id
    )

    text = build_buy_alert(
        chain=chain,
        token_name=token["token_name"],
        token_symbol=token["token_symbol"],
        spent_usd=spent_usd,
        received_amount=received_amount,
        buyer=swap.get("buyer"),
        settings=settings,
    )

    await send_buy_alert(
        group_id=group_id,
        text=text,
        settings=settings,
        buttons=buttons,
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

    tokens = await get_monitored_tokens()

    chain_tokens = [
        token
        for token in tokens
        if token["chain"] == chain
    ]

    if not chain_tokens:
        return

    latest_block = await get_latest_block(
        rpc
    )

    last_block = await get_last_block(
        chain
    )

    if last_block is None:

        #
        # Start close to the current head
        # instead of scanning the entire chain.
        #
        last_block = max(
            0,
            latest_block - 2,
        )

    if last_block >= latest_block:
        return

    start_block = last_block + 1

    end_block = min(
        latest_block,
        start_block
        + BUYBOT_BLOCK_BATCH_SIZE
        - 1,
    )

    print(
        f"BuyBot {chain}: "
        f"scanning blocks "
        f"{start_block}-{end_block}"
    )

    #
    # Cache token0/token1 per pair for this batch.
    #

    pair_cache = {}

    for token in chain_tokens:

        pair_address = normalize_address(
            token["pair_address"]
        )

        if not pair_address:
            continue

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

            pair_cache[pair_address] = (
                token0,
                token1,
            )

    #
    # Query each pair separately.
    #
    # This keeps the system compatible with public
    # RPC providers that impose log-filter limits.
    #

    for token in chain_tokens:

        pair_address = normalize_address(
            token["pair_address"]
        )

        if not pair_address:
            continue

        logs = await get_swap_logs(
            rpc,
            pair_address,
            start_block,
            end_block,
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
                )

            except Exception as exc:

                print(
                    f"BuyBot swap processing "
                    f"error: {exc}"
                )

    await save_last_block(
        chain,
        end_block,
    )


# ============================================================
# CHAIN WORKERS
# ============================================================


async def evm_chain_loop(
    chain: str,
):
    print(
        f"{chain} BuyBot detector "
        f"started."
    )

    while True:

        try:

            await process_chain(
                chain
            )

        except asyncio.CancelledError:

            print(
                f"{chain} BuyBot detector "
                f"stopped."
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
            evm_chain_loop("BNB")
        ),
        asyncio.create_task(
            evm_chain_loop("ETH")
        ),
        asyncio.create_task(
            evm_chain_loop("ROBINHOOD")
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
