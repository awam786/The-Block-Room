import asyncio
import math
from datetime import datetime, timezone

from database.connection import get_pool

from services.dex import get_best_token_pair


UPDATE_SECONDS = 30
INACTIVITY_MINUTES = 10

MARKET_CAP_LOW = 50_000
MARKET_CAP_MEDIUM = 100_000
MARKET_CAP_HIGH = 500_000

SUPPORTED_CHAINS = {
    "bnb",
    "ethereum",
    "solana",
    "robinhood",
}


def safe_number(value, default=0.0):
    try:
        if value is None:
            return default

        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except (TypeError, ValueError):
        return default


def clamp(value, minimum=0.0, maximum=100.0):
    return max(
        minimum,
        min(maximum, value),
    )


def log_score(value):
    value = safe_number(value)

    if value <= 0:
        return 0.0

    return clamp(
        math.log10(value + 1) * 10
    )


def percentage_score(value):
    value = abs(safe_number(value))

    if value <= 0:
        return 0.0

    return clamp(value)


def calculate_market_score(data):
    """
    Natural market score.

    Weights:
        30% 24h volume
        20% liquidity
        15% market cap
        15% buy activity
        10% price momentum
         5% recent activity

    The natural component intentionally totals 95%.
    Paid placement is handled separately.
    """

    volume_24h = safe_number(
        data.get("volume_24h")
    )

    liquidity = safe_number(
        data.get("liquidity_usd")
    )

    market_cap = safe_number(
        data.get("market_cap")
    )

    buys_24h = safe_number(
        data.get("buys_24h")
    )

    sells_24h = safe_number(
        data.get("sells_24h")
    )

    price_change_1h = safe_number(
        data.get("price_change_1h")
    )

    volume_5m = safe_number(
        data.get("volume_5m")
    )

    buys_5m = safe_number(
        data.get("buys_5m")
    )

    volume_score = log_score(
        volume_24h
    )

    liquidity_score = log_score(
        liquidity
    )

    market_cap_score = log_score(
        market_cap
    )

    total_trades = buys_24h + sells_24h

    if total_trades > 0:
        buy_ratio = (
            buys_24h / total_trades
        ) * 100
    else:
        buy_ratio = 0

    buy_activity_score = clamp(
        buy_ratio
    )

    momentum_score = clamp(
        50 + (
            price_change_1h * 2
        )
    )

    recent_activity_raw = (
        log_score(volume_5m)
        + log_score(buys_5m * 100)
    ) / 2

    recent_activity_score = clamp(
        recent_activity_raw
    )

    score = (
        volume_score * 0.30
        + liquidity_score * 0.20
        + market_cap_score * 0.15
        + buy_activity_score * 0.15
        + momentum_score * 0.10
        + recent_activity_score * 0.05
    )

    return round(
        max(0.0, score),
        4,
    )


def calculate_paid_boost(data):
    """
    Paid promotion placement boost.

    This is intentionally separate from the natural
    95% market score.
    """

    market_cap = safe_number(
        data.get("market_cap")
    )

    volume_24h = safe_number(
        data.get("volume_24h")
    )

    buys_5m = safe_number(
        data.get("buys_5m")
    )

    volume_5m = safe_number(
        data.get("volume_5m")
    )

    active_now = (
        buys_5m > 0
        or volume_5m > 0
    )

    if not active_now:
        return 0.0

    if market_cap >= MARKET_CAP_HIGH:
        return 50.0

    if market_cap >= MARKET_CAP_MEDIUM:
        return 30.0

    if market_cap >= MARKET_CAP_LOW:
        return 15.0

    if volume_24h > 0:
        return 5.0

    return 0.0


def has_recent_activity(data):
    volume_5m = safe_number(
        data.get("volume_5m")
    )

    buys_5m = safe_number(
        data.get("buys_5m")
    )

    sells_5m = safe_number(
        data.get("sells_5m")
    )

    return (
        volume_5m > 0
        or buys_5m > 0
        or sells_5m > 0
    )


def is_market_active(data):
    volume_24h = safe_number(
        data.get("volume_24h")
    )

    volume_5m = safe_number(
        data.get("volume_5m")
    )

    buys_24h = safe_number(
        data.get("buys_24h")
    )

    sells_24h = safe_number(
        data.get("sells_24h")
    )

    return (
        volume_24h > 0
        or volume_5m > 0
        or buys_24h > 0
        or sells_24h > 0
    )


def get_final_score(
    market_score,
    paid_boost,
):
    return round(
        safe_number(market_score)
        + safe_number(paid_boost),
        4,
    )


async def get_active_trends():
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                id,
                order_id,
                user_id,
                chain,
                token_address,
                token_name,
                token_symbol,
                duration_hours,
                amount,
                status,
                created_at,
                activated_at,
                expires_at,
                rank,
                market_score,
                market_cap,
                volume_24h,
                liquidity_usd,
                buy_activity,
                price_change_1h,
                last_activity_at
            FROM trends
            WHERE status = 'ACTIVE'
            ORDER BY
                COALESCE(rank, 999999),
                created_at ASC;
            """
        )

        return rows


async def get_trend_by_id(trend_id):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT
                id,
                order_id,
                user_id,
                chain,
                token_address,
                token_name,
                token_symbol,
                duration_hours,
                amount,
                status,
                created_at,
                activated_at,
                expires_at,
                rank,
                market_score,
                market_cap,
                volume_24h,
                liquidity_usd,
                buy_activity,
                price_change_1h,
                last_activity_at
            FROM trends
            WHERE id = $1
            LIMIT 1;
            """,
            trend_id,
        )


async def update_trend_market_data(
    trend_id,
    data,
    score,
    last_activity_at=None,
):
    pool = await get_pool()

    market_cap = safe_number(
        data.get("market_cap")
    )

    volume_24h = safe_number(
        data.get("volume_24h")
    )

    liquidity_usd = safe_number(
        data.get("liquidity_usd")
    )

    buys_24h = safe_number(
        data.get("buys_24h")
    )

    sells_24h = safe_number(
        data.get("sells_24h")
    )

    total_trades = (
        buys_24h + sells_24h
    )

    buy_activity = (
        (buys_24h / total_trades) * 100
        if total_trades > 0
        else 0
    )

    price_change_1h = safe_number(
        data.get("price_change_1h")
    )

    if last_activity_at is None:
        await connection_update(
            trend_id,
            market_cap,
            volume_24h,
            liquidity_usd,
            buy_activity,
            price_change_1h,
            score,
        )

        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE trends
            SET
                market_score = $2,
                market_cap = $3,
                volume_24h = $4,
                liquidity_usd = $5,
                buy_activity = $6,
                price_change_1h = $7,
                last_activity_at = $8
            WHERE id = $1;
            """,
            trend_id,
            score,
            market_cap,
            volume_24h,
            liquidity_usd,
            buy_activity,
            price_change_1h,
            last_activity_at,
        )


async def connection_update(
    trend_id,
    market_cap,
    volume_24h,
    liquidity_usd,
    buy_activity,
    price_change_1h,
    score,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE trends
            SET
                market_score = $2,
                market_cap = $3,
                volume_24h = $4,
                liquidity_usd = $5,
                buy_activity = $6,
                price_change_1h = $7
            WHERE id = $1;
            """,
            trend_id,
            score,
            market_cap,
            volume_24h,
            liquidity_usd,
            buy_activity,
            price_change_1h,
        )


async def update_trend_rank(
    trend_id,
    rank,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE trends
            SET rank = $2
            WHERE id = $1;
            """,
            trend_id,
            rank,
        )


async def remove_inactive_trend(
    trend_id,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE trends
            SET
                status = 'EXPIRED',
                rank = NULL
            WHERE id = $1
              AND status = 'ACTIVE';
            """,
            trend_id,
        )


async def expire_old_trends():
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE trends
            SET
                status = 'EXPIRED',
                rank = NULL
            WHERE status = 'ACTIVE'
              AND expires_at IS NOT NULL
              AND expires_at <= NOW();
            """
        )


def parse_last_activity(
    value,
):
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value


def inactive_too_long(
    last_activity_at,
):
    if last_activity_at is None:
        return False

    last_activity_at = parse_last_activity(
        last_activity_at
    )

    now = datetime.now(
        timezone.utc
    )

    elapsed = (
        now - last_activity_at
    ).total_seconds()

    return (
        elapsed
        >= INACTIVITY_MINUTES * 60
    )


def extract_activity_time(
    data,
    existing_last_activity,
):
    """
    Keep the existing timestamp unless fresh
    five-minute activity is detected.
    """

    if not has_recent_activity(data):
        return existing_last_activity

    return datetime.now(
        timezone.utc
    )


async def process_trend(
    trend,
):
    trend_id = trend["id"]
    chain = (
        str(trend["chain"])
        .strip()
        .lower()
    )
    token_address = (
        trend["token_address"]
    )

    if chain not in SUPPORTED_CHAINS:
        return {
            "trend_id": trend_id,
            "active": False,
            "reason": "UNSUPPORTED_CHAIN",
        }

    try:
        pair = await get_best_token_pair(
            chain,
            token_address,
        )
    except Exception as exc:
        print(
            "Trending market-data error "
            f"trend={trend_id}: {exc}"
        )

        return {
            "trend_id": trend_id,
            "active": True,
            "reason": "MARKET_DATA_ERROR",
        }

    if not pair:
        if inactive_too_long(
            trend["last_activity_at"]
        ):
            await remove_inactive_trend(
                trend_id
            )

            return {
                "trend_id": trend_id,
                "active": False,
                "reason": "INACTIVE",
            }

        return {
            "trend_id": trend_id,
            "active": True,
            "reason": "NO_PAIR",
        }

    data = pair

    active = is_market_active(
        data
    )

    previous_activity = (
        trend["last_activity_at"]
    )

    last_activity = extract_activity_time(
        data,
        previous_activity,
    )

    if (
        not active
        and inactive_too_long(
            previous_activity
        )
    ):
        await remove_inactive_trend(
            trend_id
        )

        return {
            "trend_id": trend_id,
            "active": False,
            "reason": "INACTIVE",
        }

    market_score = calculate_market_score(
        data
    )

    paid_boost = calculate_paid_boost(
        data
    )

    final_score = get_final_score(
        market_score,
        paid_boost,
    )

    await update_trend_market_data(
        trend_id=trend_id,
        data=data,
        score=final_score,
        last_activity_at=last_activity,
    )

    return {
        "trend_id": trend_id,
        "active": True,
        "score": final_score,
        "market_score": market_score,
        "paid_boost": paid_boost,
        "market_cap": safe_number(
            data.get("market_cap")
        ),
        "volume_24h": safe_number(
            data.get("volume_24h")
        ),
        "liquidity_usd": safe_number(
            data.get("liquidity_usd")
        ),
    }


async def recalculate_ranks():
    trends = await get_active_trends()

    scored = []

    for trend in trends:
        try:
            result = await process_trend(
                trend
            )

            if result.get("active"):
                scored.append(
                    {
                        "trend": trend,
                        "result": result,
                    }
                )

        except Exception as exc:
            print(
                "Trend processing error "
                f"trend={trend['id']}: {exc}"
            )

    scored.sort(
        key=lambda item: (
            safe_number(
                item["result"].get(
                    "score"
                )
            ),
            safe_number(
                item["result"].get(
                    "volume_24h"
                )
            ),
            safe_number(
                item["result"].get(
                    "liquidity_usd"
                )
            ),
        ),
        reverse=True,
    )

    for index, item in enumerate(
        scored,
        start=1,
    ):
        await update_trend_rank(
            item["trend"]["id"],
            index,
        )

    return scored


async def trending_engine_worker():
    print(
        "Trending engine worker started."
    )

    while True:
        try:
            await expire_old_trends()

            await recalculate_ranks()

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                "Trending engine error: "
                f"{exc}"
            )

        await asyncio.sleep(
            UPDATE_SECONDS
        )
