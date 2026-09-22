import asyncio
import time
from decimal import Decimal, InvalidOperation

import httpx
from telegram import Bot

from config import (
    BOT_TOKEN,
    ETHEREUM_RPC_URL,
    BNB_RPC_URL,
    ROBINHOOD_RPC_URL,
    BUYBOT_POLL_SECONDS,
    BUYBOT_BLOCK_BATCH_SIZE,
)

from database.connection import get_pool


# ============================================================
# CONFIG
# ============================================================

EVM_CHAINS = {
    "ethereum": {
        "name": "Ethereum",
        "rpc": ETHEREUM_RPC_URL,
        "native_symbol": "ETH",
    },
    "bnb": {
        "name": "BNB Smart Chain",
        "rpc": BNB_RPC_URL,
        "native_symbol": "BNB",
    },
    "robinhood": {
        "name": "Robinhood",
        "rpc": ROBINHOOD_RPC_URL,
        "native_symbol": "ETH",
    },
}


# ERC-20 Transfer(address,address,uint256)
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)


# ============================================================
# STATE
# ============================================================

last_checked_blocks = {}


# ============================================================
# RPC HELPERS
# ============================================================

async def rpc_call(
    client: httpx.AsyncClient,
    rpc_url: str,
    method: str,
    params: list,
):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
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
):
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


async def get_block(
    client: httpx.AsyncClient,
    rpc_url: str,
    block_number: int,
):
    return await rpc_call(
        client,
        rpc_url,
        "eth_getBlockByNumber",
        [
            hex(block_number),
            True,
        ],
    )


async def get_transaction_receipt(
    client: httpx.AsyncClient,
    rpc_url: str,
    tx_hash: str,
):
    return await rpc_call(
        client,
        rpc_url,
        "eth_getTransactionReceipt",
        [tx_hash],
    )


async def get_logs(
    client: httpx.AsyncClient,
    rpc_url: str,
    from_block: int,
    to_block: int,
):
    return await rpc_call(
        client,
        rpc_url,
        "eth_getLogs",
        [
            {
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "topics": [
                    TRANSFER_TOPIC
                ],
            }
        ],
    )


# ============================================================
# ADDRESS HELPERS
# ============================================================

def normalize_address(
    value: str | None,
):
    if not value:
        return ""

    return value.lower()


def topic_address(
    topic: str | None,
):
    if not topic:
        return ""

    value = topic[-40:]

    return (
        "0x"
        + value.lower()
    )


# ============================================================
# NUMBER HELPERS
# ============================================================

def hex_to_int(
    value: str | None,
):
    if not value:
        return 0

    try:
        return int(
            value,
            16,
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0


def decimal_amount(
    raw_value: int,
    decimals: int,
):
    try:
        return Decimal(raw_value) / (
            Decimal(10) ** decimals
        )
    except (
        InvalidOperation,
        ValueError,
    ):
        return Decimal("0")


# ============================================================
# TOKEN METADATA
# ============================================================

async def get_token_metadata(
    client: httpx.AsyncClient,
    rpc_url: str,
    token_address: str,
):
    """
    Reads ERC-20 symbol and decimals directly
    from the token contract.

    This keeps the detector independent from
    DexScreener for the actual on-chain detection.
    """

    token_address = token_address.lower()

    try:
        symbol_result = await rpc_call(
            client,
            rpc_url,
            "eth_call",
            [
                {
                    "to": token_address,
                    "data": "0x95d89b41",
                },
                "latest",
            ],
        )

        decimals_result = await rpc_call(
            client,
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

        decimals = hex_to_int(
            decimals_result
        )

        symbol = decode_erc20_string(
            symbol_result
        )

        return {
            "symbol": symbol,
            "decimals": decimals,
        }

    except Exception:
        return {
            "symbol": None,
            "decimals": 18,
        }


def decode_erc20_string(
    value: str | None,
):
    if not value:
        return None

    if value.startswith("0x"):
        value = value[2:]

    try:
        raw = bytes.fromhex(value)

        # ABI dynamic string
        if len(raw) >= 96:
            length = int.from_bytes(
                raw[32:64],
                "big",
            )

            start = 64
            end = start + length

            return raw[start:end].decode(
                "utf-8",
                errors="ignore",
            )

        # bytes32-style return
        return raw.rstrip(
            b"\x00"
        ).decode(
            "utf-8",
            errors="ignore",
        )

    except Exception:
        return None


# ============================================================
# MONITORED TOKENS
# ============================================================

async def get_monitored_tokens(
    chain: str,
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id,
                group_id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                pair_address,
                dex_url,
                enabled
            FROM buybot_tokens
            WHERE chain = $1
              AND enabled = TRUE
            ORDER BY id ASC
            """,
            chain,
        )

    return rows


# ============================================================
# TOKEN LOOKUP
# ============================================================

def build_token_map(
    rows,
):
    result = {}

    for row in rows:
        address = normalize_address(
            row["contract_address"]
        )

        if not address:
            continue

        result[address] = row

    return result


# ============================================================
# TRANSFER LOG PARSING
# ============================================================

def parse_transfer_log(
    log,
):
    topics = log.get(
        "topics",
        [],
    )

    if len(topics) < 3:
        return None

    token_address = normalize_address(
        log.get("address")
    )

    sender = topic_address(
        topics[1]
    )

    receiver = topic_address(
        topics[2]
    )

    raw_amount = hex_to_int(
        log.get("data")
    )

    return {
        "token_address": token_address,
        "sender": sender,
        "receiver": receiver,
        "raw_amount": raw_amount,
        "transaction_hash": log.get(
            "transactionHash"
        ),
        "block_number": hex_to_int(
            log.get("blockNumber")
        ),
        "log_index": hex_to_int(
            log.get("logIndex")
        ),
    }


# ============================================================
# TRANSACTION ANALYSIS
# ============================================================

async def get_transaction(
    client: httpx.AsyncClient,
    rpc_url: str,
    tx_hash: str,
):
    return await rpc_call(
        client,
        rpc_url,
        "eth_getTransactionByHash",
        [tx_hash],
    )


def transaction_sender(
    transaction,
):
    if not transaction:
        return ""

    return normalize_address(
        transaction.get("from")
    )


def transaction_value(
    transaction,
):
    if not transaction:
        return Decimal("0")

    return decimal_amount(
        hex_to_int(
            transaction.get("value")
        ),
        18,
    )


# ============================================================
# BUY CLASSIFICATION
# ============================================================

def classify_buy(
    transfer,
    transaction,
):
    """
    A token transfer is considered a potential buy when:

    1. The transaction sender is the token recipient.
    2. The token recipient is not the same as the
       token contract itself.
    3. The transfer is an inbound token movement.

    This is intentionally conservative.

    DEX-specific interpretation can later be added for
    stronger buy/sell classification.
    """

    if not transfer or not transaction:
        return False

    sender = transaction_sender(
        transaction
    )

    receiver = normalize_address(
        transfer["receiver"]
    )

    if not sender or not receiver:
        return False

    if sender != receiver:
        return False

    token_address = normalize_address(
        transfer["token_address"]
    )

    if sender == token_address:
        return False

    if transfer["raw_amount"] <= 0:
        return False

    return True


# ============================================================
# EVENT DEDUPLICATION
# ============================================================

async def event_exists(
    group_id: int,
    chain: str,
    tx_hash: str,
    token_address: str,
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id
            FROM buybot_events
            WHERE group_id = $1
              AND chain = $2
              AND tx_hash = $3
              AND LOWER(token_address) = LOWER($4)
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
    buyer_address: str,
    received_amount: Decimal,
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO buybot_events (
                    group_id,
                    chain,
                    tx_hash,
                    token_address,
                    token_symbol,
                    buyer_address,
                    received_amount
                )
                VALUES (
                    $1,
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
                DO NOTHING
                """,
                group_id,
                chain,
                tx_hash,
                token_address,
                token_symbol,
                buyer_address,
                received_amount,
            )

            return True

        except Exception:
            return False


# ============================================================
# USD ESTIMATION
# ============================================================

async def get_pair_usd_data(
    dex_url: str | None,
):
    """
    Gets the latest USD pair information from
    DexScreener when a pair URL is available.
    """

    if not dex_url:
        return None

    try:
        async with httpx.AsyncClient(
            timeout=8
        ) as client:
            response = await client.get(
                dex_url
            )

            if response.status_code != 200:
                return None

            data = response.json()

            if not isinstance(
                data,
                dict,
            ):
                return None

            return data

    except Exception:
        return None


async def estimate_spent_usd(
    client: httpx.AsyncClient,
    rpc_url: str,
    transaction,
    token_amount: Decimal,
    token_address: str,
):
    """
    Basic fallback estimation.

    Native-value transactions are converted only when
    a reliable price source is available later.

    Returning None is intentional rather than inventing
    a USD value.
    """

    value = transaction_value(
        transaction
    )

    if value > 0:
        return None

    return None


# ============================================================
# BUYBOT MESSAGE
# ============================================================

async def get_group_settings(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM buybot_settings
            WHERE group_id = $1
            """,
            group_id,
        )

    return row


async def get_group_buttons(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                button_name,
                button_url
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC
            """,
            group_id,
        )


def format_buyer(
    address: str,
):
    if not address:
        return "Unknown"

    if len(address) <= 12:
        return address

    return (
        address[:6]
        + "..."
        + address[-4:]
    )


def format_token_amount(
    amount: Decimal,
):
    if amount >= Decimal("1000000"):
        return f"{amount:,.2f}"

    if amount >= Decimal("1"):
        return f"{amount:,.4f}"

    return f"{amount:.8f}"


def build_buy_message(
    settings,
    chain_name: str,
    token_name: str | None,
    token_symbol: str | None,
    spent_usd,
    received_amount: Decimal,
    buyer: str,
    tx_hash: str,
):
    name = (
        token_name
        or "Token"
    )

    symbol = (
        token_symbol
        or "TOKEN"
    )

    title = (
        settings["alert_title"]
        or "🚀 New Buy Detected"
    )

    template = (
        settings["alert_template"]
        or
        (
            "{name} (${symbol})\n\n"
            "{spent} spent\n"
            "{received} received\n"
            "{chain}\n"
            "Buyer: {buyer}"
        )
    )

    if spent_usd is None:
        spent_text = "Amount unavailable"
    else:
        spent_text = (
            f"${spent_usd:,.2f}"
        )

    replacements = {
        "{name}": name,
        "{symbol}": symbol,
        "{spent}": spent_text,
        "{received}": (
            f"{format_token_amount(received_amount)} "
            f"{symbol}"
        ),
        "{market_cap}": "Unavailable",
        "{chain}": chain_name,
        "{buyer}": format_buyer(buyer),
        "{tx}": tx_hash,
    }

    message = template

    for key, value in replacements.items():
        message = message.replace(
            key,
            str(value),
        )

    return (
        f"{title}\n\n"
        f"{message}"
    )


# ============================================================
# SEND ALERT
# ============================================================

async def send_buy_alert(
    bot: Bot,
    group_id: int,
    chain_name: str,
    token_name: str | None,
    token_symbol: str | None,
    spent_usd,
    received_amount: Decimal,
    buyer: str,
    tx_hash: str,
    media_type: str | None,
    media_id: str | None,
):
    settings = await get_group_settings(
        group_id
    )

    if not settings:
        return

    if not settings["enabled"]:
        return

    min_buy = settings[
        "min_buy_usd"
    ]

    if (
        spent_usd is not None
        and min_buy is not None
        and Decimal(str(spent_usd))
        < Decimal(str(min_buy))
    ):
        return

    text = build_buy_message(
        settings,
        chain_name,
        token_name,
        token_symbol,
        spent_usd,
        received_amount,
        buyer,
        tx_hash,
    )

    buttons = await get_group_buttons(
        group_id
    )

    keyboard = []

    for button in buttons:
        keyboard.append(
            [
                {
                    "text": button[
                        "button_name"
                    ],
                    "url": button[
                        "button_url"
                    ],
                }
            ]
        )

    # Telegram accepts InlineKeyboardMarkup
    # objects rather than raw dictionaries.
    from telegram import (
        InlineKeyboardButton,
        InlineKeyboardMarkup,
    )

    inline_buttons = []

    for row in buttons:
        inline_buttons.append(
            [
                InlineKeyboardButton(
                    row["button_name"],
                    url=row["button_url"],
                )
            ]
        )

    reply_markup = (
        InlineKeyboardMarkup(
            inline_buttons
        )
        if inline_buttons
        else None
    )

    try:
        if (
            media_type == "photo"
            and media_id
        ):
            await bot.send_photo(
                chat_id=group_id,
                photo=media_id,
                caption=text,
                reply_markup=reply_markup,
            )

        elif (
            media_type == "animation"
            and media_id
        ):
            await bot.send_animation(
                chat_id=group_id,
                animation=media_id,
                caption=text,
                reply_markup=reply_markup,
            )

        elif (
            media_type == "video"
            and media_id
        ):
            await bot.send_video(
                chat_id=group_id,
                video=media_id,
                caption=text,
                reply_markup=reply_markup,
            )

        else:
            await bot.send_message(
                chat_id=group_id,
                text=text,
                reply_markup=reply_markup,
            )

    except Exception as exc:
        print(
            "BuyBot Telegram alert error:",
            exc,
        )


# ============================================================
# PROCESS TOKEN TRANSFER
# ============================================================

async def process_transfer(
    client: httpx.AsyncClient,
    bot: Bot,
    chain: str,
    chain_config: dict,
    transfer: dict,
    token_row,
):
    tx_hash = transfer[
        "transaction_hash"
    ]

    if not tx_hash:
        return

    group_id = token_row[
        "group_id"
    ]

    token_address = normalize_address(
        token_row[
            "contract_address"
        ]
    )

    if await event_exists(
        group_id,
        chain,
        tx_hash,
        token_address,
    ):
        return

    transaction = await get_transaction(
        client,
        chain_config["rpc"],
        tx_hash,
    )

    if not classify_buy(
        transfer,
        transaction,
    ):
        return

    metadata = await get_token_metadata(
        client,
        chain_config["rpc"],
        token_address,
    )

    decimals = metadata.get(
        "decimals",
        18,
    )

    token_symbol = (
        token_row["token_symbol"]
        or metadata.get("symbol")
    )

    received_amount = decimal_amount(
        transfer["raw_amount"],
        decimals,
    )

    if received_amount <= 0:
        return

    spent_usd = await estimate_spent_usd(
        client,
        chain_config["rpc"],
        transaction,
        received_amount,
        token_address,
    )

    saved = await save_event(
        group_id,
        chain,
        tx_hash,
        token_address,
        token_symbol,
        transfer["sender"],
        received_amount,
    )

    if not saved:
        return

    settings = await get_group_settings(
        group_id
    )

    if not settings:
        return

    await send_buy_alert(
        bot=bot,
        group_id=group_id,
        chain_name=chain_config["name"],
        token_name=token_row[
            "token_name"
        ],
        token_symbol=token_symbol,
        spent_usd=spent_usd,
        received_amount=received_amount,
        buyer=transfer["sender"],
        tx_hash=tx_hash,
        media_type=settings[
            "media_type"
        ],
        media_id=settings[
            "media_id"
        ],
    )


# ============================================================
# PROCESS BLOCK RANGE
# ============================================================

async def process_block_range(
    client: httpx.AsyncClient,
    bot: Bot,
    chain: str,
    chain_config: dict,
    from_block: int,
    to_block: int,
):
    tokens = await get_monitored_tokens(
        chain
    )

    if not tokens:
        return

    token_map = build_token_map(
        tokens
    )

    try:
        logs = await get_logs(
            client,
            chain_config["rpc"],
            from_block,
            to_block,
        )

    except Exception as exc:
        print(
            f"{chain} log query error:",
            exc,
        )
        return

    for log in logs or []:
        transfer = parse_transfer_log(
            log
        )

        if not transfer:
            continue

        token_address = transfer[
            "token_address"
        ]

        token_row = token_map.get(
            token_address
        )

        if not token_row:
            continue

        try:
            await process_transfer(
                client,
                bot,
                chain,
                chain_config,
                transfer,
                token_row,
            )

        except Exception as exc:
            print(
                f"{chain} transfer processing error:",
                exc,
            )


# ============================================================
# CHAIN WORKER
# ============================================================

async def run_chain(
    client: httpx.AsyncClient,
    bot: Bot,
    chain: str,
    chain_config: dict,
):
    rpc_url = chain_config[
        "rpc"
    ]

    try:
        latest = await get_block_number(
            client,
            rpc_url,
        )
    except Exception as exc:
        print(
            f"{chain} RPC error:",
            exc,
        )
        return

    previous = last_checked_blocks.get(
        chain
    )

    if previous is None:
        # Start from a small recent window.
        previous = max(
            0,
            latest - BUYBOT_BLOCK_BATCH_SIZE,
        )

    if previous >= latest:
        return

    to_block = min(
        latest,
        previous + BUYBOT_BLOCK_BATCH_SIZE,
    )

    await process_block_range(
        client,
        bot,
        chain,
        chain_config,
        previous + 1,
        to_block,
    )

    last_checked_blocks[
        chain
    ] = to_block


# ============================================================
# MAIN EVM WORKER
# ============================================================

async def evm_detector_worker():
    print(
        "EVM BuyBot detector started."
    )

    bot = Bot(
        token=BOT_TOKEN
    )

    timeout = httpx.Timeout(
        20.0,
        connect=10.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout
    ) as client:

        while True:
            try:
                for chain, config in EVM_CHAINS.items():
                    try:
                        await run_chain(
                            client,
                            bot,
                            chain,
                            config,
                        )

                    except Exception as exc:
                        print(
                            f"{chain} detector error:",
                            exc,
                        )

                await asyncio.sleep(
                    BUYBOT_POLL_SECONDS
                )

            except asyncio.CancelledError:
                print(
                    "EVM BuyBot detector stopped."
                )
                raise

            except Exception as exc:
                print(
                    "EVM detector worker error:",
                    exc,
                )

                await asyncio.sleep(
                    max(
                        5,
                        BUYBOT_POLL_SECONDS,
                    )
                )


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":
    asyncio.run(
        evm_detector_worker()
    )
