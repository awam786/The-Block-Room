import asyncio
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from database.connection import get_pool

from services.dex import get_best_token_pair


# ============================================================
# ENGINE CONFIGURATION
# ============================================================

POLL_SECONDS = 30

INACTIVITY_MINUTES = 10

LOW_MARKET_CAP = 50_000.0
MEDIUM_MARKET_CAP = 100_000.0
HIGH_MARKET_CAP = 500_000.0


# Natural market score:
# 30% 24h volume
# 20% liquidity
# 15% market cap
# 15% buy activity
# 10% price momentum
# 5% recent activity
#
# These natural components total 95%.
#
# Paid promotion is handled separately as a
# placement boost so it does not distort the
# natural market metrics.

NATURAL_WEIGHT_VOLUME = 0.30
NATURAL_WEIGHT_LIQUIDITY = 0.20
NATURAL_WEIGHT_MARKET_CAP = 0.15
NATURAL_WEIGHT_BUYS = 0.15
NATURAL_WEIGHT_MOMENTUM = 0.10
NATURAL_WEIGHT_RECENT_ACTIVITY = 0.05


SUPPORTED_CHAINS = {
    "bnb",
    "ethereum",
    "solana",
    "robinhood",
}


# ============================================================
# HELPERS
# ============================================================

def number(
    value: Any,
) -> float:
    try:
        return float(
            value or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def clamp(
    value: float,
    minimum: float = 0.0,
    maximum: float = 100.0,
) -> float:
    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


def log_score(
    value: float,
    reference: float,
) -> float:
    if value <= 0:
        return 0.0

    if reference <= 0:
        return 0.0

    return clamp(
        (
            math.log10(
                value + 1
            )
            / math.log10(
                reference + 1
            )
        )
        * 100
    )


def calculate_market_score(
    market_cap: float,
    volume_24h: float,
    liquidity_usd: float,
    buy_activity: float,
    price_change_1h: float,
    recent_activity: float,
) -> float:
    volume_score = log_score(
        volume_24h,
        1_000_000,
    )

    liquidity_score = log_score(
        liquidity_usd,
        500_000,
    )

    market_cap_score = log_score(
        market_cap,
        1_000_000,
    )

    buy_score = log_score(
        buy_activity,
        1_000,
    )

    momentum_score = clamp(
        50
        + (
            price_change_1h
            * 2
        )
    )

    recent_score = clamp(
        recent_activity
    )

    return (
        volume_score
        * NATURAL_WEIGHT_VOLUME
        + liquidity_score
        * NATURAL_WEIGHT_LIQUIDITY
        + market_cap_score
        * NATURAL_WEIGHT_MARKET_CAP
        + buy_score
        * NATURAL_WEIGHT_BUYS
        + momentum_score
        * NATURAL_WEIGHT_MOMENTUM
        + recent_score
        * NATURAL_WEIGHT_RECENT_ACTIVITY
    )


def calculate_paid_boost(
    market_cap: float,
    volume_24h: float,
    liquidity_usd: float,
    buy_activity: float,
) -> float:
    has_activity = (
        volume_24h > 0
        or buy_activity > 0
        or liquidity_usd > 0
    )

    if not has_activity:
        return 0.0

    if market_cap >= HIGH_MARKET_CAP:
        return 50.0

    if market_cap >= MEDIUM_MARKET_CAP:
        return 30.0

    if market_cap >= LOW_MARKET_CAP:
        return 15.0

    return 5.0


def placement_priority(
    market_cap: float,
    volume_24h: float,
    liquidity_usd: float,
    buy_activity: float,
    paid_boost: float,
) -> float:
    """
    Paid promotion is a placement mechanism,
    not a replacement for real market activity.

    The resulting priority strongly rewards:
    - activity
    - liquidity
    - volume
    - market cap
    - paid promotion

    A completely inactive token still receives
    zero priority and is removed separately.
    """

    activity = (
        volume_24h
        + (
            liquidity_usd
            * 0.20
        )
        + (
            buy_activity
            * 100
        )
    )

    market_cap_factor = 0.0

    if market_cap >= HIGH_MARKET_CAP:
        market_cap_factor = 50.0
    elif market_cap >= MEDIUM_MARKET_CAP:
        market_cap_factor = 30.0
    elif market_cap >= LOW_MARKET_CAP:
        market_cap_factor = 15.0
    elif market_cap > 0:
        market_cap_factor = 5.0

    return (
        activity
        + market_cap_factor
        + (
            paid_boost
            * 10
        )
    )


def activity_value(
    volume_24h: float,
    buy_activity: float,
    liquidity_usd: float,
) -> float:
    return (
        volume_24h
        + (
            buy_activity
            * 100
        )
        + (
            liquidity_usd
            * 0.10
        )
    )


# ============================================================
# DATABASE
# ============================================================

async def get_active_trends():
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetch(
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
                market_cap,
                volume_24h,
                liquidity_usd,
                buy_activity,
                price_change_1h,
                market_score,
                rank,
                last_activity_at,
                activated_at,
                expires_at
            FROM trends
            WHERE status = 'ACTIVE'
              AND (
                    expires_at IS NULL
                    OR expires_at > NOW()
              )
            ORDER BY rank ASC NULLS LAST;
            """
        )


async def update_trend_market_data(
    trend_id: int,
    market_cap: float,
    volume_24h: float,
    liquidity_usd: float,
    buy_activity: float,
    price_change_1h: float,
    market_score: float,
    last_activity_at: Optional[datetime],
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE trends
            SET
                market_cap = $2,
                volume_24h = $3,
                liquidity_usd = $4,
                buy_activity = $5,
                price_change_1h = $6,
                market_score = $7,
                last_activity_at = $8
            WHERE id = $1;
            """,
            trend_id,
            market_cap,
            volume_24h,
            liquidity_usd,
            buy_activity,
            price_change_1h,
            market_score,
            last_activity_at,
        )


async def expire_trend(
    trend_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE trends
            SET
                status = 'EXPIRED'
            WHERE id = $1
              AND status = 'ACTIVE';
            """,
            trend_id,
        )


async def update_trend_rank(
    trend_id: int,
    rank: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE trends
            SET rank = $2
            WHERE id = $1
              AND status = 'ACTIVE';
            """,
            trend_id,
            rank,
        )


# ============================================================
# MARKET ACTIVITY
# ============================================================

def extract_market_data(
    pair: Dict[str, Any],
):
    market_cap = number(
        pair.get("market_cap")
    )

    volume_24h = number(
        pair.get("volume_24h")
    )

    liquidity_usd = number(
        pair.get("liquidity_usd")
    )

    buys_24h = number(
        pair.get("buys_24h")
    )

    sells_24h = number(
        pair.get("sells_24h")
    )

    price_change_1h = number(
        pair.get("price_change_1h")
    )

    recent_volume = (
        number(
            pair.get("volume_5m")
        )
        + number(
            pair.get("volume_1h")
        )
    )

    recent_buys = (
        number(
            pair.get("buys_5m")
        )
        + number(
            pair.get("buys_1h")
        )
    )

    buy_activity = (
        buys_24h
        + (
            recent_buys
            * 5
        )
    )

    recent_activity = clamp(
        log_score(
            recent_volume
            + (
                recent_buys
                * 100
            ),
            100_000,
        )
    )

    return (
        market_cap,
        volume_24h,
        liquidity_usd,
        buy_activity,
        price_change_1h,
        recent_activity,
        buys_24h,
        sells_24h,
    )


def activity_is_present(
    volume_24h: float,
    liquidity_usd: float,
    buy_activity: float,
    recent_activity: float,
) -> bool:
    return (
        volume_24h > 0
        or liquidity_usd > 0
        or buy_activity > 0
        or recent_activity > 0
    )


# ============================================================
# LAST ACTIVITY
# ============================================================

def determine_last_activity(
    current_last_activity,
    volume_24h: float,
    buy_activity: float,
    recent_activity: float,
    pair: Dict[str, Any],
):
    now = datetime.now(
        timezone.utc
    )

    recent_volume = (
        number(
            pair.get("volume_5m")
        )
        + number(
            pair.get("volume_1h")
        )
    )

    recent_buys = (
        number(
            pair.get("buys_5m")
        )
        + number(
            pair.get("buys_1h")
        )
    )

    has_recent_activity = (
        recent_volume > 0
        or recent_buys > 0
    )

    if has_recent_activity:
        return now

    if (
        current_last_activity
        is not None
    ):
        return current_last_activity

    # If this is the first engine observation
    # and the token has only historical 24h
    # activity, start the inactivity clock now.
    if (
        volume_24h > 0
        or buy_activity > 0
        or recent_activity > 0
    ):
        return now

    return None


def is_inactive(
    last_activity_at,
) -> bool:
    if last_activity_at is None:
        return True

    if (
        last_activity_at.tzinfo
        is None
    ):
        last_activity_at = (
            last_activity_at.replace(
                tzinfo=timezone.utc
            )
        )

    age_seconds = (
        datetime.now(
            timezone.utc
        )
        - last_activity_at
    ).total_seconds()

    return (
        age_seconds
        >= INACTIVITY_MINUTES * 60
    )


# ============================================================
# UPDATE ONE TREND
# ============================================================

async def process_trend(
    trend,
):
    trend_id = trend["id"]
    chain = (
        str(
            trend["chain"]
            or ""
        )
        .strip()
        .lower()
    )

    token_address = (
        trend["token_address"]
    )

    if chain not in SUPPORTED_CHAINS:
        await expire_trend(
            trend_id
        )
        return None

    try:
        pair = await get_best_token_pair(
            chain,
            token_address,
        )
    except Exception as exc:
        print(
            "Trending market lookup error "
            f"trend={trend_id}: {exc}"
        )
        return None

    if not pair:
        # If a previously live trend suddenly
        # has no pair, do not immediately remove
        # it. The next cycle may restore it.
        return None

    (
        market_cap,
        volume_24h,
        liquidity_usd,
        buy_activity,
        price_change_1h,
        recent_activity,
        buys_24h,
        sells_24h,
    ) = extract_market_data(
        pair
    )

    last_activity_at = (
        determine_last_activity(
            trend["last_activity_at"],
            volume_24h,
            buy_activity,
            recent_activity,
            pair,
        )
    )

    if (
        last_activity_at is None
        or is_inactive(
            last_activity_at
        )
    ):
        await expire_trend(
            trend_id
        )

        print(
            "Trend removed for inactivity: "
            f"{trend_id}"
        )

        return None

    market_score = (
        calculate_market_score(
            market_cap,
            volume_24h,
            liquidity_usd,
            buy_activity,
            price_change_1h,
            recent_activity,
        )
    )

    paid_boost = (
        calculate_paid_boost(
            market_cap,
            volume_24h,
            liquidity_usd,
            buy_activity,
        )
    )

    priority = (
        placement_priority(
            market_cap,
            volume_24h,
            liquidity_usd,
            buy_activity,
            paid_boost,
        )
    )

    await update_trend_market_data(
        trend_id=trend_id,
        market_cap=market_cap,
        volume_24h=volume_24h,
        liquidity_usd=liquidity_usd,
        buy_activity=buy_activity,
        price_change_1h=price_change_1h,
        market_score=market_score,
        last_activity_at=last_activity_at,
    )

    return {
        "id": trend_id,
        "market_cap": market_cap,
        "volume_24h": volume_24h,
        "liquidity_usd": liquidity_usd,
        "buy_activity": buy_activity,
        "price_change_1h": price_change_1h,
        "recent_activity": recent_activity,
        "buys_24h": buys_24h,
        "sells_24h": sells_24h,
        "market_score": market_score,
        "paid_boost": paid_boost,
        "priority": priority,
        "last_activity_at": last_activity_at,
    }


# ============================================================
# RANKING
# ============================================================

async def rank_active_trends(
    results,
):
    if not results:
        return

    # Highest priority first.
    #
    # Paid boost affects placement, but real
    # activity remains part of the priority.
    ordered = sorted(
        results,
        key=lambda item: (
            item["priority"],
            item["market_score"],
            item["volume_24h"],
            item["liquidity_usd"],
            item["buy_activity"],
        ),
        reverse=True,
    )

    for rank, item in enumerate(
        ordered,
        start=1,
    ):
        await update_trend_rank(
            item["id"],
            rank,
        )


# ============================================================
# EXPIRATION CLEANUP
# ============================================================

async def expire_old_trends():
    pool = await get_pool()

    async with pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE trends
            SET status = 'EXPIRED'
            WHERE status = 'ACTIVE'
              AND expires_at IS NOT NULL
              AND expires_at <= NOW();
            """
        )

    if result != "UPDATE 0":
        print(
            "Expired old trending orders: "
            f"{result}"
        )


# ============================================================
# ENGINE LOOP
# ============================================================

async def trending_engine_worker():
    print(
        "Trending engine started."
    )

    while True:
        try:
            await expire_old_trends()

            trends = (
                await get_active_trends()
            )

            if not trends:
                await asyncio.sleep(
                    POLL_SECONDS
                )
                continue

            results = []

            for trend in trends:
                try:
                    result = (
                        await process_trend(
                            trend
                        )
                    )

                    if result:
                        results.append(
                            result
                        )

                except asyncio.CancelledError:
                    raise

                except Exception as exc:
                    print(
                        "Trend processing error "
                        f"trend={trend['id']}: "
                        f"{exc}"
                    )

            if results:
                await rank_active_trends(
                    results
                )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                "Trending engine error: "
                f"{exc}"
            )

        await asyncio.sleep(
            POLL_SECONDS
        )
