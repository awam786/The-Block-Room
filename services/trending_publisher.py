import asyncio

from telegram import Bot

from config import (
    BOT_TOKEN,
    TRENDING_CHANNEL_ID,
)

from database.connection import get_pool


MAX_DISPLAYED_TRENDS = 10

DEFAULT_PUBLISH_INTERVAL_SECONDS = 30

MESSAGE_SETTING_KEY = (
    "trending_channel_message_id"
)


def format_money(value):
    try:
        value = float(value or 0)

    except (
        ValueError,
        TypeError,
    ):
        value = 0

    if value >= 1_000_000_000:
        return (
            f"${value / 1_000_000_000:.2f}B"
        )

    if value >= 1_000_000:
        return (
            f"${value / 1_000_000:.2f}M"
        )

    if value >= 1_000:
        return (
            f"${value / 1_000:.2f}K"
        )

    return f"${value:.2f}"


def format_price_change(value):
    try:
        value = float(value or 0)

    except (
        ValueError,
        TypeError,
    ):
        value = 0

    return f"{value:+.2f}%"


def chain_emoji(chain):
    return {
        "bnb": "🟡",
        "ethereum": "🔷",
        "solana": "🟣",
        "robinhood": "🟠",
    }.get(
        str(chain).lower(),
        "🌐",
    )


async def get_ranked_trends():
    pool = get_pool()

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT
                id,
                chain,
                contract_address,
                token_name,
                token_symbol,
                score,
                volume_24h,
                market_cap,
                liquidity,
                price_change_24h,
                buys_24h,
                sells_24h,
                rank,
                paid_promotion,
                expires_at
            FROM trends
            WHERE status = 'ACTIVE'
              AND rank IS NOT NULL
            ORDER BY rank ASC
            LIMIT $1
            """,
            MAX_DISPLAYED_TRENDS,
        )

    return rows


async def get_setting(
    key: str,
):
    pool = get_pool()

    async with pool.acquire() as conn:

        return await conn.fetchval(
            """
            SELECT value
            FROM system_settings
            WHERE key = $1
            """,
            key,
        )


async def save_channel_message_id(
    message_id: int,
):
    pool = get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO system_settings (
                key,
                value,
                updated_at
            )
            VALUES (
                $1,
                $2,
                NOW()
            )
            ON CONFLICT (key)
            DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = NOW()
            """,
            MESSAGE_SETTING_KEY,
            str(message_id),
        )


def format_expiry(
    expires_at,
):
    if not expires_at:
        return None

    try:
        remaining = (
            expires_at
            - __import__(
                "datetime"
            ).datetime.now(
                __import__(
                    "datetime"
                ).timezone.utc
            )
        )

        seconds = int(
            remaining.total_seconds()
        )

        if seconds <= 0:
            return "Expired"

        minutes = seconds // 60

        hours = minutes // 60

        minutes %= 60

        if hours > 0:
            return f"{hours}h {minutes}m"

        return f"{minutes}m"

    except Exception:
        return None


async def build_trending_message():
    trends = await get_ranked_trends()

    if not trends:

        return (
            "🏛️ *THE BLOCK ROOM — TRENDING*\n\n"
            "🔎 No active tokens are currently trending.\n\n"
            "📡 Market activity is being monitored "
            "continuously."
        )

    lines = [
        "🏛️ *THE BLOCK ROOM — TRENDING*",
        "",
        "🔥 *LIVE MARKET BOARD*",
        "",
    ]

    for trend in trends:

        rank = (
            trend["rank"]
            or 0
        )

        token_name = (
            trend["token_name"]
            or "Unknown Token"
        )

        symbol = (
            trend["token_symbol"]
            or "UNKNOWN"
        )

        chain = (
            trend["chain"]
            or "unknown"
        )

        market_cap = format_money(
            trend["market_cap"]
        )

        volume = format_money(
            trend["volume_24h"]
        )

        liquidity = format_money(
            trend["liquidity"]
        )

        price_change = format_price_change(
            trend["price_change_24h"]
        )

        score = float(
            trend["score"]
            or 0
        )

        buys = int(
            trend["buys_24h"]
            or 0
        )

        sells = int(
            trend["sells_24h"]
            or 0
        )

        paid_marker = (
            " 💎"
            if trend["paid_promotion"]
            else ""
        )

        emoji = chain_emoji(
            chain
        )

        lines.append(
            f"*#{rank}* {emoji} "
            f"*{token_name}* "
            f"({symbol})"
            f"{paid_marker}"
        )

        lines.append(
            f"💰 MC: {market_cap}   "
            f"📊 Vol: {volume}"
        )

        lines.append(
            f"💧 Liq: {liquidity}   "
            f"📈 {price_change}"
        )

        lines.append(
            f"🟢 Buys: {buys}   "
            f"🔴 Sells: {sells}"
        )

        lines.append(
            f"⭐ Score: {score:.2f}"
        )

        remaining = format_expiry(
            trend["expires_at"]
        )

        if remaining:

            lines.append(
                f"⏳ Remaining: {remaining}"
            )

        lines.append("")

    lines.extend(
        [
            "━━━━━━━━━━━━━━━━━━",
            "🔄 *Rankings update automatically*",
            "📡 Powered by The Block Room",
        ]
    )

    return "\n".join(lines)


async def publish_trending():

    if not BOT_TOKEN:

        print(
            "Trending publisher: "
            "BOT_TOKEN is missing."
        )

        return

    if not TRENDING_CHANNEL_ID:

        print(
            "Trending publisher: "
            "TRENDING_CHANNEL_ID is not configured."
        )

        return

    try:

        channel_id = int(
            TRENDING_CHANNEL_ID
        )

    except (
        ValueError,
        TypeError,
    ):

        print(
            "Trending publisher: "
            "TRENDING_CHANNEL_ID must be "
            "a valid Telegram ID."
        )

        return

    text = await build_trending_message()

    bot = Bot(
        token=BOT_TOKEN
    )

    existing_message_id = await get_setting(
        MESSAGE_SETTING_KEY
    )

    # --------------------------------------------------------
    # EDIT EXISTING MESSAGE
    # --------------------------------------------------------

    if existing_message_id:

        try:

            await bot.edit_message_text(
                chat_id=channel_id,
                message_id=int(
                    existing_message_id
                ),
                text=text,
                parse_mode="Markdown",
            )

            return

        except Exception as exc:

            print(
                "Could not edit existing "
                f"trending message: {exc}"
            )

    # --------------------------------------------------------
    # CREATE NEW MESSAGE
    # --------------------------------------------------------

    try:

        message = await bot.send_message(
            chat_id=channel_id,
            text=text,
            parse_mode="Markdown",
        )

        await save_channel_message_id(
            message.message_id
        )

        print(
            "Trending channel message created."
        )

    except Exception as exc:

        print(
            "Could not publish trending "
            f"message: {exc}"
        )

    finally:

        try:
            await bot.shutdown()

        except Exception:
            pass


async def trending_publisher_worker():

    print(
        "Trending channel publisher started."
    )

    while True:

        try:

            await publish_trending()

        except asyncio.CancelledError:

            raise

        except Exception as exc:

            print(
                f"Trending publisher error: {exc}"
            )

        await asyncio.sleep(
            DEFAULT_PUBLISH_INTERVAL_SECONDS
        )
