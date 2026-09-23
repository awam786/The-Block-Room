import asyncio
from datetime import datetime, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from config import BOT_TOKEN, TRENDING_CHANNEL_ID
from database.connection import get_pool


PUBLISH_SECONDS = 30
TOP_TRENDS = 10

publisher_bot = None


CHAIN_LABELS = {
    "bnb": "BNB Chain",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "robinhood": "Robinhood",
}

CHAIN_EMOJIS = {
    "bnb": "🟡",
    "ethereum": "🔷",
    "solana": "🟣",
    "robinhood": "🔵",
}


def format_money(value):
    if value is None:
        return "$0"

    try:
        value = float(value)
    except (TypeError, ValueError):
        return "$0"

    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.2f}K"

    return f"${value:,.2f}"


def format_price(value):
    if value is None:
        return "N/A"

    try:
        value = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if value == 0:
        return "$0"

    if abs(value) >= 1:
        return f"${value:,.4f}"

    if abs(value) >= 0.01:
        return f"${value:,.6f}"

    return f"${value:.10f}".rstrip("0").rstrip(".")


def format_percentage(value):
    if value is None:
        return "0.00%"

    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0.00%"

    sign = "+" if value > 0 else ""

    return f"{sign}{value:.2f}%"


def format_remaining(expires_at):
    if expires_at is None:
        return "N/A"

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    now = datetime.now(timezone.utc)

    seconds = int(
        (expires_at - now).total_seconds()
    )

    if seconds <= 0:
        return "Expired"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


def safe_text(value, fallback="Unknown"):
    if value is None:
        return fallback

    value = str(value).strip()

    if not value:
        return fallback

    return value


async def get_top_trends():
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                id,
                order_id,
                chain,
                token_address,
                token_name,
                token_symbol,
                duration_hours,
                amount,
                status,
                rank,
                market_score,
                market_cap,
                volume_24h,
                liquidity_usd,
                buy_activity,
                price_change_1h,
                expires_at
            FROM trends
            WHERE status = 'ACTIVE'
            ORDER BY
                rank ASC NULLS LAST,
                market_score DESC NULLS LAST,
                volume_24h DESC NULLS LAST
            LIMIT $1;
            """,
            TOP_TRENDS,
        )

        return rows


def explorer_url(chain, token_address):
    token_address = safe_text(
        token_address,
        "",
    )

    if not token_address:
        return None

    chain = str(chain).lower()

    if chain == "ethereum":
        return (
            "https://etherscan.io/token/"
            f"{token_address}"
        )

    if chain == "bnb":
        return (
            "https://bscscan.com/token/"
            f"{token_address}"
        )

    if chain == "solana":
        return (
            "https://solscan.io/token/"
            f"{token_address}"
        )

    if chain == "robinhood":
        return (
            "https://explorer.mainnet.chain.robinhood.com/"
            f"address/{token_address}"
        )

    return None


def build_token_url(chain, token_address):
    return explorer_url(
        chain,
        token_address,
    )


def build_trending_message(rows):
    lines = [
        "🔥 THE BLOCK ROOM — LIVE TRENDING",
        "",
        "Real-time promoted tokens currently "
        "active on The Block Room.",
        "",
    ]

    for index, row in enumerate(
        rows,
        start=1,
    ):
        chain = str(
            row["chain"] or ""
        ).lower()

        chain_label = CHAIN_LABELS.get(
            chain,
            chain.upper() or "Unknown",
        )

        chain_emoji = CHAIN_EMOJIS.get(
            chain,
            "🌐",
        )

        name = safe_text(
            row["token_name"],
            "Unknown Token",
        )

        symbol = safe_text(
            row["token_symbol"],
            "TOKEN",
        )

        market_cap = format_money(
            row["market_cap"]
        )

        volume = format_money(
            row["volume_24h"]
        )

        liquidity = format_money(
            row["liquidity_usd"]
        )

        buy_activity = row[
            "buy_activity"
        ]

        if buy_activity is None:
            buy_activity_text = "N/A"
        else:
            try:
                buy_activity_text = (
                    f"{float(buy_activity):.1f}%"
                )
            except (
                TypeError,
                ValueError,
            ):
                buy_activity_text = "N/A"

        price_change = format_percentage(
            row["price_change_1h"]
        )

        remaining = format_remaining(
            row["expires_at"]
        )

        lines.extend(
            [
                (
                    f"#{index}  {chain_emoji} "
                    f"{name} (${symbol})"
                ),
                (
                    f"   ├ MC: {market_cap}  "
                    f"• 24H Vol: {volume}"
                ),
                (
                    f"   ├ Liquidity: {liquidity}  "
                    f"• Buys: {buy_activity_text}"
                ),
                (
                    f"   ├ 1H: {price_change}  "
                    f"• {chain_label}"
                ),
                (
                    f"   └ Remaining: {remaining}"
                ),
                "",
            ]
        )

    if not rows:
        lines = [
            "🔥 THE BLOCK ROOM — LIVE TRENDING",
            "",
            "No tokens are currently active.",
            "",
            "Use /trend to promote a token.",
        ]

    return "\n".join(lines)


def build_keyboard(rows):
    keyboard = []

    for row in rows:
        chain = str(
            row["chain"] or ""
        ).lower()

        address = row[
            "token_address"
        ]

        url = build_token_url(
            chain,
            address,
        )

        if not url:
            continue

        symbol = safe_text(
            row["token_symbol"],
            "Token",
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🔎 {symbol}",
                    url=url,
                )
            ]
        )

    return (
        InlineKeyboardMarkup(keyboard)
        if keyboard
        else None
    )


async def get_existing_message_id():
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT value
            FROM system_settings
            WHERE key = 'trending_channel_message_id'
            LIMIT 1;
            """
        )

        if not row:
            return None

        try:
            return int(row["value"])
        except (
            TypeError,
            ValueError,
        ):
            return None


async def save_message_id(message_id):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO system_settings (
                key,
                value
            )
            VALUES (
                'trending_channel_message_id',
                $1
            )
            ON CONFLICT (key)
            DO UPDATE SET
                value = EXCLUDED.value;
            """,
            str(message_id),
        )


async def publish_trending():
    global publisher_bot

    if not TRENDING_CHANNEL_ID:
        print(
            "Trending publisher skipped: "
            "TRENDING_CHANNEL_ID is not configured."
        )
        return

    rows = await get_top_trends()

    text = build_trending_message(
        rows
    )

    keyboard = build_keyboard(
        rows
    )

    message_id = (
        await get_existing_message_id()
    )

    if publisher_bot is None:
        publisher_bot = Bot(
            token=BOT_TOKEN
        )

    if message_id:
        try:
            await publisher_bot.edit_message_text(
                chat_id=TRENDING_CHANNEL_ID,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )

            return

        except Exception as exc:
            print(
                "Trending message edit failed: "
                f"{exc}"
            )

    try:
        message = (
            await publisher_bot.send_message(
                chat_id=TRENDING_CHANNEL_ID,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        )

        await save_message_id(
            message.message_id
        )

    except Exception as exc:
        print(
            "Trending channel publish error: "
            f"{exc}"
        )


async def trending_publisher_worker():
    global publisher_bot

    print(
        "Trending publisher worker started."
    )

    publisher_bot = Bot(
        token=BOT_TOKEN
    )

    try:
        while True:
            try:
                await publish_trending()

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                print(
                    "Trending publisher error: "
                    f"{exc}"
                )

            await asyncio.sleep(
                PUBLISH_SECONDS
            )

    finally:
        if publisher_bot is not None:
            try:
                await publisher_bot.shutdown()
            except Exception as exc:
                print(
                    "Trending publisher shutdown "
                    f"error: {exc}"
                )

            publisher_bot = None
