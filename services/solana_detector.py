import asyncio
from decimal import Decimal, InvalidOperation

import httpx

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from config import (
    BOT_TOKEN,
    HELIUS_API_KEY,
    BUYBOT_POLL_SECONDS,
)

from database.connection import get_pool


# ============================================================
# CONFIGURATION
# ============================================================

HELIUS_BASE_URL = (
    "https://api.helius.xyz/v0"
)

SOLANA_RPC_URL = (
    "https://mainnet.helius-rpc.com/"
)

SOLANA_DECIMALS = 9

SOLANA_CHAIN = "solana"


# ============================================================
# WORKER STATE
# ============================================================

last_seen_signatures = {}


# ============================================================
# DATABASE
# ============================================================

async def get_monitored_tokens():
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
            WHERE chain = 'solana'
              AND enabled = TRUE
            ORDER BY id ASC
            """
        )

    return rows


async def get_buybot_settings(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT *
            FROM buybot_settings
            WHERE group_id = $1
            """,
            group_id,
        )


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


async def event_exists(
    group_id: int,
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
              AND chain = 'solana'
              AND tx_hash = $2
              AND LOWER(token_address) = LOWER($3)
            LIMIT 1
            """,
            group_id,
            tx_hash,
            token_address,
        )

    return row is not None


async def save_event(
    group_id: int,
    tx_hash: str,
    token_address: str,
    token_symbol: str | None,
    buyer_address: str | None,
    spent_amount_usd,
    received_amount: Decimal,
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        try:
            result = await conn.execute(
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
                DO NOTHING
                """,
                group_id,
                tx_hash,
                token_address,
                token_symbol,
                buyer_address,
                spent_amount_usd,
                received_amount,
            )

            return result.endswith(
                "1"
            )

        except Exception as exc:
            print(
                "Solana event save error:",
                exc,
            )
            return False


# ============================================================
# HELIUS API
# ============================================================

async def helius_get(
    client: httpx.AsyncClient,
    endpoint: str,
    params: dict | None = None,
):
    if not HELIUS_API_KEY:
        raise RuntimeError(
            "HELIUS_API_KEY is not configured."
        )

    request_params = dict(
        params or {}
    )

    request_params[
        "api-key"
    ] = HELIUS_API_KEY

    response = await client.get(
        endpoint,
        params=request_params,
    )

    response.raise_for_status()

    return response.json()


async def get_token_signatures(
    client: httpx.AsyncClient,
    token_address: str,
):
    """
    Fetch recent signatures involving the token mint.

    The Solana RPC getSignaturesForAddress method
    is used because it is available through Helius RPC.
    """

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [
            token_address,
            {
                "limit": 100,
            },
        ],
    }

    response = await client.post(
        SOLANA_RPC_URL,
        params={
            "api-key": HELIUS_API_KEY,
        },
        json=payload,
    )

    response.raise_for_status()

    data = response.json()

    if data.get("error"):
        raise RuntimeError(
            str(data["error"])
        )

    return data.get(
        "result",
        [],
    )


async def get_transaction(
    client: httpx.AsyncClient,
    signature: str,
):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    }

    response = await client.post(
        SOLANA_RPC_URL,
        params={
            "api-key": HELIUS_API_KEY,
        },
        json=payload,
    )

    response.raise_for_status()

    data = response.json()

    if data.get("error"):
        raise RuntimeError(
            str(data["error"])
        )

    return data.get(
        "result"
    )


# ============================================================
# HELIUS ENHANCED TRANSACTION API
# ============================================================

async def get_enhanced_transaction(
    client: httpx.AsyncClient,
    signature: str,
):
    """
    Helius Enhanced Transactions API provides
    parsed transaction information and can identify
    swap activity more reliably than raw SPL transfers.
    """

    endpoint = (
        f"{HELIUS_BASE_URL}/transactions"
    )

    params = {
        "transactions": signature,
    }

    try:
        data = await helius_get(
            client,
            endpoint,
            params,
        )

        if isinstance(
            data,
            list,
        ) and data:
            return data[0]

    except Exception as exc:
        print(
            "Helius enhanced transaction error:",
            exc,
        )

    return None


# ============================================================
# TOKEN AMOUNT HELPERS
# ============================================================

def token_balance_changes(
    transaction,
    token_address: str,
):
    """
    Reads pre/post token balances and calculates
    the largest positive balance change for the
    monitored token.

    This is useful for identifying tokens received
    by a wallet during a swap.
    """

    if not transaction:
        return []

    meta = transaction.get(
        "meta"
    )

    if not meta:
        return []

    pre_balances = meta.get(
        "preTokenBalances",
        [],
    )

    post_balances = meta.get(
        "postTokenBalances",
        [],
    )

    pre_map = {}

    for item in pre_balances:
        mint = item.get(
            "mint"
        )

        if mint != token_address:
            continue

        owner = item.get(
            "owner"
        )

        amount = (
            item.get("uiTokenAmount", {})
            .get("uiAmountString")
        )

        if amount is None:
            continue

        try:
            pre_map[
                owner
            ] = Decimal(amount)

        except (
            InvalidOperation,
            TypeError,
        ):
            continue

    changes = []

    for item in post_balances:
        mint = item.get(
            "mint"
        )

        if mint != token_address:
            continue

        owner = item.get(
            "owner"
        )

        amount = (
            item.get("uiTokenAmount", {})
            .get("uiAmountString")
        )

        if amount is None:
            continue

        try:
            post_amount = Decimal(
                amount
            )

        except (
            InvalidOperation,
            TypeError,
        ):
            continue

        previous = pre_map.get(
            owner,
            Decimal("0"),
        )

        change = (
            post_amount
            - previous
        )

        if change > 0:
            changes.append(
                {
                    "owner": owner,
                    "amount": change,
                }
            )

    return changes


# ============================================================
# SOL / USD HELPERS
# ============================================================

def native_balance_change(
    transaction,
    address: str,
):
    if not transaction:
        return Decimal("0")

    meta = transaction.get(
        "meta"
    )

    if not meta:
        return Decimal("0")

    message = (
        transaction
        .get("transaction", {})
        .get("message", {})
    )

    account_keys = message.get(
        "accountKeys",
        [],
    )

    try:
        index = None

        for i, account in enumerate(
            account_keys
        ):
            if isinstance(
                account,
                dict,
            ):
                pubkey = account.get(
                    "pubkey"
                )
            else:
                pubkey = account

            if pubkey == address:
                index = i
                break

        if index is None:
            return Decimal("0")

        pre = meta.get(
            "preBalances",
            [],
        )

        post = meta.get(
            "postBalances",
            [],
        )

        if (
            index >= len(pre)
            or index >= len(post)
        ):
            return Decimal("0")

        difference = (
            Decimal(post[index])
            - Decimal(pre[index])
        )

        return difference / Decimal(
            10 ** SOLANA_DECIMALS
        )

    except Exception:
        return Decimal("0")


# ============================================================
# BUY DETECTION
# ============================================================

def identify_buyer(
    transaction,
    token_address: str,
):
    """
    Find the owner that received the monitored token.

    The result is a candidate buyer address.
    """

    changes = token_balance_changes(
        transaction,
        token_address,
    )

    if not changes:
        return None, Decimal("0")

    changes.sort(
        key=lambda item: item["amount"],
        reverse=True,
    )

    winner = changes[0]

    return (
        winner["owner"],
        winner["amount"],
    )


def is_successful_transaction(
    transaction,
):
    if not transaction:
        return False

    meta = transaction.get(
        "meta"
    )

    if not meta:
        return False

    if meta.get(
        "err"
    ) is not None:
        return False

    return True


def enhanced_is_swap(
    enhanced,
):
    if not enhanced:
        return False

    tx_type = str(
        enhanced.get(
            "type",
            ""
        )
    ).upper()

    source = str(
        enhanced.get(
            "source",
            ""
        )
    ).upper()

    swap_types = {
        "SWAP",
        "TOKEN_SWAP",
    }

    if tx_type in swap_types:
        return True

    if "SWAP" in source:
        return True

    return False


# ============================================================
# USD PRICE
# ============================================================

async def get_sol_usd_price(
    client: httpx.AsyncClient,
):
    """
    Public price fallback.

    If unavailable, the system leaves USD amount
    unknown rather than inventing a value.
    """

    try:
        response = await client.get(
            "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112",
            timeout=8,
        )

        if response.status_code != 200:
            return None

        data = response.json()

        pairs = data.get(
            "pairs",
            [],
        )

        if not pairs:
            return None

        prices = []

        for pair in pairs:
            value = pair.get(
                "priceUsd"
            )

            if not value:
                continue

            try:
                prices.append(
                    Decimal(str(value))
                )
            except InvalidOperation:
                continue

        if not prices:
            return None

        prices.sort()

        return prices[
            len(prices) // 2
        ]

    except Exception:
        return None


# ============================================================
# BUY ALERT RENDERING
# ============================================================

def format_amount(
    value: Decimal,
):
    if value >= Decimal("1000000"):
        return f"{value:,.2f}"

    if value >= Decimal("1"):
        return f"{value:,.4f}"

    return f"{value:.8f}"


def short_address(
    address: str | None,
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


def render_message(
    settings,
    chain_name: str,
    token_name: str,
    token_symbol: str,
    spent_usd,
    received_amount: Decimal,
    buyer: str,
    tx_hash: str,
):
    title = (
        settings["alert_title"]
        or "⚡ Fresh Buy"
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
        spent = "Amount unavailable"
    else:
        spent = (
            f"${spent_usd:,.2f}"
        )

    replacements = {
        "{name}": token_name,
        "{symbol}": token_symbol,
        "{spent}": spent,
        "{received}": (
            f"{format_amount(received_amount)} "
            f"{token_symbol}"
        ),
        "{market_cap}": "Unavailable",
        "{chain}": chain_name,
        "{buyer}": short_address(
            buyer
        ),
        "{tx}": tx_hash,
    }

    for key, value in replacements.items():
        template = template.replace(
            key,
            str(value),
        )

    return (
        f"{title}\n\n"
        f"{template}"
    )


async def send_alert(
    bot: Bot,
    group_id: int,
    settings,
    buttons,
    text: str,
):
    keyboard = []

    for button in buttons:
        keyboard.append(
            [
                InlineKeyboardButton(
                    button["button_name"],
                    url=button["button_url"],
                )
            ]
        )

    markup = (
        InlineKeyboardMarkup(
            keyboard
        )
        if keyboard
        else None
    )

    media_type = settings[
        "media_type"
    ]

    media_id = settings[
        "media_id"
    ]

    try:
        if (
            media_type == "photo"
            and media_id
        ):
            await bot.send_photo(
                chat_id=group_id,
                photo=media_id,
                caption=text,
                reply_markup=markup,
            )

        elif (
            media_type == "animation"
            and media_id
        ):
            await bot.send_animation(
                chat_id=group_id,
                animation=media_id,
                caption=text,
                reply_markup=markup,
            )

        elif (
            media_type == "video"
            and media_id
        ):
            await bot.send_video(
                chat_id=group_id,
                video=media_id,
                caption=text,
                reply_markup=markup,
            )

        else:
            await bot.send_message(
                chat_id=group_id,
                text=text,
                reply_markup=markup,
            )

    except Exception as exc:
        print(
            "Solana BuyBot Telegram error:",
            exc,
        )


# ============================================================
# PROCESS TRANSACTION
# ============================================================

async def process_signature(
    client: httpx.AsyncClient,
    bot: Bot,
    token_row,
    signature: str,
):
    group_id = token_row[
        "group_id"
    ]

    token_address = token_row[
        "contract_address"
    ]

    if await event_exists(
        group_id,
        signature,
        token_address,
    ):
        return

    transaction = await get_transaction(
        client,
        signature,
    )

    if not transaction:
        return

    if not is_successful_transaction(
        transaction
    ):
        return

    buyer, received_amount = (
        identify_buyer(
            transaction,
            token_address,
        )
    )

    if not buyer:
        return

    if received_amount <= 0:
        return

    enhanced = (
        await get_enhanced_transaction(
            client,
            signature,
        )
    )

    # Only classify as a buy when the
    # transaction looks like a swap.
    if enhanced and not enhanced_is_swap(
        enhanced
    ):
        return

    spent_usd = None

    # Try to estimate SOL spent.
    sol_spent = abs(
        native_balance_change(
            transaction,
            buyer,
        )
    )

    if sol_spent > 0:
        sol_price = (
            await get_sol_usd_price(
                client
            )
        )

        if sol_price:
            spent_usd = (
                sol_spent
                * sol_price
            )

    settings = await get_buybot_settings(
        group_id
    )

    if not settings:
        return

    if not settings["enabled"]:
        return

    minimum = settings[
        "min_buy_usd"
    ]

    if (
        minimum is not None
        and Decimal(str(minimum)) > 0
    ):
        if spent_usd is None:
            # We cannot honestly verify
            # the minimum, so don't alert.
            return

        if spent_usd < Decimal(
            str(minimum)
        ):
            return

    saved = await save_event(
        group_id=group_id,
        tx_hash=signature,
        token_address=token_address,
        token_symbol=token_row[
            "token_symbol"
        ],
        buyer_address=buyer,
        spent_amount_usd=spent_usd,
        received_amount=received_amount,
    )

    if not saved:
        return

    token_name = (
        token_row["token_name"]
        or "Token"
    )

    token_symbol = (
        token_row["token_symbol"]
        or "TOKEN"
    )

    text = render_message(
        settings=settings,
        chain_name="Solana",
        token_name=token_name,
        token_symbol=token_symbol,
        spent_usd=spent_usd,
        received_amount=received_amount,
        buyer=buyer,
        tx_hash=signature,
    )

    buttons = await get_group_buttons(
        group_id
    )

    await send_alert(
        bot=bot,
        group_id=group_id,
        settings=settings,
        buttons=buttons,
        text=text,
    )


# ============================================================
# TOKEN MONITOR
# ============================================================

async def monitor_token(
    client: httpx.AsyncClient,
    bot: Bot,
    token_row,
):
    token_id = token_row[
        "id"
    ]

    token_address = token_row[
        "contract_address"
    ]

    try:
        signatures = (
            await get_token_signatures(
                client,
                token_address,
            )
        )

    except Exception as exc:
        print(
            "Solana signature error:",
            exc,
        )
        return

    if not signatures:
        return

    signatures = list(
        reversed(signatures)
    )

    last_seen = last_seen_signatures.get(
        token_id
    )

    if last_seen:
        new_items = []

        for item in signatures:
            if item.get(
                "signature"
            ) == last_seen:
                new_items = []

                # Continue from the next item.
                found = False

                for candidate in signatures:
                    if found:
                        new_items.append(
                            candidate
                        )

                    if candidate.get(
                        "signature"
                    ) == last_seen:
                        found = True

                break

        else:
            new_items = signatures

    else:
        # First run: process only the most recent
        # transaction window to avoid flooding the group.
        new_items = signatures[-10:]

    if not new_items:
        return

    for item in new_items:
        signature = item.get(
            "signature"
        )

        if not signature:
            continue

        try:
            await process_signature(
                client,
                bot,
                token_row,
                signature,
            )
        except Exception as exc:
            print(
                "Solana transaction processing error:",
                exc,
            )

        last_seen_signatures[
            token_id
        ] = signature


# ============================================================
# MAIN SOLANA WORKER
# ============================================================

async def solana_detector_worker():
    print(
        "Solana BuyBot detector started."
    )

    if not HELIUS_API_KEY:
        print(
            "WARNING: HELIUS_API_KEY is not configured."
        )

    bot = Bot(
        token=BOT_TOKEN
    )

    timeout = httpx.Timeout(
        25.0,
        connect=10.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout
    ) as client:

        while True:
            try:
                tokens = (
                    await get_monitored_tokens()
                )

                if tokens:
                    for token in tokens:
                        try:
                            await monitor_token(
                                client,
                                bot,
                                token,
                            )
                        except Exception as exc:
                            print(
                                "Solana token monitor error:",
                                exc,
                            )

                await asyncio.sleep(
                    max(
                        3,
                        BUYBOT_POLL_SECONDS,
                    )
                )

            except asyncio.CancelledError:
                print(
                    "Solana BuyBot detector stopped."
                )
                raise

            except Exception as exc:
                print(
                    "Solana detector worker error:",
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
        solana_detector_worker()
    )
