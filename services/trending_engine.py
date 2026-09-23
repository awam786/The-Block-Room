import asyncio
import math
from datetime import datetime, timezone

from database.connection import get_pool

from services.dex import (
    get_token_pairs,
    choose_best_pair,
    parse_pair,
)


DEFAULT_UPDATE_SECONDS = 30
DEFAULT_INACTIVITY_MINUTES = 10

LOW_MC_THRESHOLD = 50_000
HIGH_MC_THRESHOLD = 100_000
TOP_MC_THRESHOLD = 500_000


async def get_setting(
    key: str,
    default: float,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT value
            FROM system_settings
            WHERE key = $1
            """,
            key,
        )

    if value is None:
        return default

    try:
        return float(value)

    except (
        ValueError,
        TypeError,
    ):
        return default


def safe_number(value):
    try:
        return float(value or 0)

    except (
        ValueError,
        TypeError,
    ):
        return 0.0


def logarithmic_score(
    value: float,
    maximum: float,
):
    if value <= 0:
        return 0.0

    if maximum <= 0:
        return 0.0

    return min(
        100.0,
        (
            math.log10(value + 1)
            / math.log10(maximum + 1)
        )
        * 100.0,
    )


def calculate_market_score(
    volume_24h: float,
    liquidity: float,
    market_cap: float,
    buys_24h: float,
    price_change_24h: float,
    recent_activity: float,
):
    volume_score = logarithmic_score(
        volume_24h,
        10_000_000,
    )

    liquidity_score = logarithmic_score(
        liquidity,
        5_000_000,
    )

    market_cap_score = logarithmic_score(
        market_cap,
        50_000_000,
    )

    buy_score = logarithmic_score(
        buys_24h,
        10_000,
    )

    momentum = max(
        -100.0,
        min(
            100.0,
            price_change_24h,
        ),
    )

    momentum_score = (
        (momentum + 100.0)
        / 200.0
        * 100.0
    )

    recent_score = max(
        0.0,
        min(
            100.0,
            recent_activity,
        ),
    )

    return (
        volume_score * 0.30
        + liquidity_score * 0.20
        + market_cap_score * 0.15
        + buy_score * 0.15
        + momentum_score * 0.10
        + recent_score * 0.05
    )


def calculate_paid_placement_score(
    market_score: float,
    market_cap: float,
    has_activity: bool,
):
    """
    Paid promotion is handled separately from the
    natural market score.

    This provides paid listings with visibility while
    preventing an inactive low-cap token from reaching #1
    simply because it paid for promotion.
    """

    if not has_activity:
        return 0.0

    if market_cap >= TOP_MC_THRESHOLD:
        return 50.0

    if market_cap >= HIGH_MC_THRESHOLD:
        return 30.0

    if market_cap >= LOW_MC_THRESHOLD:
        return 15.0

    return 5.0


def calculate_final_score(
    market_score: float,
    paid_score: float,
):
    return (
        market_score
        + paid_score
    )


async def fetch_market_data(
    chain: str,
    contract_address: str,
):
    try:
        pairs = await get_token_pairs(
            chain,
            contract_address,
        )

        pair = choose_best_pair(
            pairs
        )

        if not pair:
            return None

        return parse_pair(
            pair
        )

    except Exception as exc:
        print(
            f"Market data error for "
            f"{chain}:{contract_address}: {exc}"
        )

        return None


async def get_active_trends():
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                t.id,
                t.order_id,
                t.chain,
                t.contract_address,
                t.token_name,
                t.token_symbol,
                t.market_cap,
                t.volume_24h,
                t.liquidity,
                t.price_change_24h,
                t.buys_24h,
                t.sells_24h,
                t.score,
                t.rank,
                t.paid_promotion,
                t.last_activity_at,
                t.last_volume_24h,
                t.inactivity_started_at,
                t.started_at,
                t.expires_at
            FROM trends t
            WHERE t.status = 'ACTIVE'
            ORDER BY t.id ASC
            """
        )

    return rows


def has_meaningful_activity(
    market_data: dict,
    previous_volume: float,
):
    volume = safe_number(
        market_data.get(
            "volume_24h"
        )
    )

    buys = safe_number(
        market_data.get(
            "buys_24h"
        )
    )

    sells = safe_number(
        market_data.get(
            "sells_24h"
        )
    )

    total_transactions = (
        buys + sells
    )

    if total_transactions > 0:
        return True

    if volume > previous_volume:
        return True

    return False


async def update_trend_market_data(
    trend,
    market_data: dict,
    market_score: float,
    final_score: float,
):
    pool = get_pool()

    now = datetime.now(
        timezone.utc
    )

    volume = safe_number(
        market_data.get(
            "volume_24h"
        )
    )

    market_cap = safe_number(
        market_data.get(
            "market_cap"
        )
    )

    liquidity = safe_number(
        market_data.get(
            "liquidity_usd"
        )
    )

    price_change = safe_number(
        market_data.get(
            "price_change_24h"
        )
    )

    buys = int(
        safe_number(
            market_data.get(
                "buys_24h"
            )
        )
    )

    sells = int(
        safe_number(
            market_data.get(
                "sells_24h"
            )
        )
    )

    previous_volume = safe_number(
        trend["last_volume_24h"]
    )

    active_now = has_meaningful_activity(
        market_data,
        previous_volume,
    )

    inactivity_started_at = (
        trend["inactivity_started_at"]
    )

    last_activity_at = (
        trend["last_activity_at"]
    )

    if active_now:
        last_activity_at = now
        inactivity_started_at = None

    elif inactivity_started_at is None:
        inactivity_started_at = now

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE trends
            SET
                score = $1,
                volume_24h = $2,
                market_cap = $3,
                liquidity = $4,
                price_change_24h = $5,
                buys_24h = $6,
                sells_24h = $7,
                last_activity_at = $8,
                last_volume_24h = $9,
                inactivity_started_at = $10,
                updated_at = NOW()
            WHERE id = $11
            """,
            final_score,
            volume,
            market_cap,
            liquidity,
            price_change,
            buys,
            sells,
            last_activity_at,
            volume,
            inactivity_started_at,
            trend["id"],
        )

    return {
        "active": active_now,
        "market_score": market_score,
        "final_score": final_score,
        "market_cap": market_cap,
        "volume": volume,
    }


async def remove_inactive_trend(
    trend_id: int,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE trends
            SET
                status = 'REMOVED_INACTIVE',
                rank = NULL,
                updated_at = NOW()
            WHERE id = $1
              AND status = 'ACTIVE'
            """,
            trend_id,
        )


async def remove_expired_trends():
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE trends
            SET
                status = 'EXPIRED',
                rank = NULL,
                updated_at = NOW()
            WHERE status = 'ACTIVE'
              AND expires_at IS NOT NULL
              AND expires_at <= NOW()
            RETURNING id, order_id
            """
        )

        for row in rows:
            await conn.execute(
                """
                UPDATE orders
                SET
                    status = 'EXPIRED',
                    updated_at = NOW()
                WHERE id = $1
                """,
                row["order_id"],
            )

    return rows


def determine_paid_priority(
    market_cap: float,
    market_score: float,
    has_activity: bool,
):
    """
    Preferred visibility area:

    Below $50K:
        #3-#5 area

    $50K-$100K:
        #3-#4 area

    Above $100K:
        #2-#3 area

    $500K+:
        Eligible for #1
    """

    if not has_activity:
        return 5

    if market_cap >= TOP_MC_THRESHOLD:
        return 1

    if market_cap >= HIGH_MC_THRESHOLD:
        return 3

    if market_cap >= LOW_MC_THRESHOLD:
        return 4

    return 5


async def assign_ranks(
    evaluated_trends,
):
    """
    Hybrid ranking system.

    Natural market performance remains important.

    Paid promotion provides protected visibility
    according to market-cap and activity rules.

    An inactive paid token cannot buy #1.
    """

    if not evaluated_trends:
        return []

    paid_candidates = []
    normal_candidates = []

    for item in evaluated_trends:

        if item["paid_promotion"]:

            priority = determine_paid_priority(
                item["market_cap"],
                item["market_score"],
                item["has_activity"],
            )

            item["paid_priority"] = priority

            paid_candidates.append(
                item
            )

        else:
            normal_candidates.append(
                item
            )

    normal_candidates.sort(
        key=lambda item: (
            item["market_score"],
            item["final_score"],
        ),
        reverse=True,
    )

    paid_candidates.sort(
        key=lambda item: (
            item["paid_priority"],
            -item["market_score"],
        )
    )

    final_order = []

    used_ids = set()

    # --------------------------------------------------------
    # Eligible $500K+ paid tokens.
    # Strongest eligible paid token can occupy #1.
    # --------------------------------------------------------

    top_paid = [
        item
        for item in paid_candidates
        if (
            item["paid_priority"] == 1
            and item["has_activity"]
        )
    ]

    if top_paid:

        top_paid.sort(
            key=lambda item: (
                item["market_score"],
                item["final_score"],
            ),
            reverse=True,
        )

        winner = top_paid[0]

        final_order.append(
            winner
        )

        used_ids.add(
            winner["id"]
        )

    # --------------------------------------------------------
    # Remaining paid listings.
    # --------------------------------------------------------

    remaining_paid = [
        item
        for item in paid_candidates
        if item["id"] not in used_ids
    ]

    remaining_paid.sort(
        key=lambda item: (
            item["paid_priority"],
            -item["market_score"],
        )
    )

    for paid in remaining_paid:

        desired_rank = paid[
            "paid_priority"
        ]

        if desired_rank <= 1:
            desired_rank = 2

        elif desired_rank <= 3:
            desired_rank = 3

        elif desired_rank <= 4:
            desired_rank = 4

        else:
            desired_rank = 5

        insert_index = min(
            max(
                desired_rank - 1,
                0,
            ),
            len(final_order),
        )

        final_order.insert(
            insert_index,
            paid,
        )

        used_ids.add(
            paid["id"]
        )

    # --------------------------------------------------------
    # Fill remaining positions with organic listings.
    # --------------------------------------------------------

    for item in normal_candidates:

        if item["id"] in used_ids:
            continue

        final_order.append(
            item
        )

    # --------------------------------------------------------
    # Protect the intended paid visibility area.
    # --------------------------------------------------------

    protected_paid = [
        item
        for item in final_order
        if (
            item["paid_promotion"]
            and item["has_activity"]
            and item["market_cap"]
            < TOP_MC_THRESHOLD
        )
    ]

    for item in protected_paid:

        if item["market_cap"] < LOW_MC_THRESHOLD:
            target_rank = 5

        elif item["market_cap"] < HIGH_MC_THRESHOLD:
            target_rank = 4

        else:
            target_rank = 3

        try:
            current_index = final_order.index(
                item
            )

        except ValueError:
            continue

        current_rank = (
            current_index + 1
        )

        if current_rank > target_rank:

            final_order.pop(
                current_index
            )

            target_index = min(
                target_rank - 1,
                len(final_order),
            )

            final_order.insert(
                target_index,
                item,
            )

    # --------------------------------------------------------
    # Final rule:
    #
    # An active $500K+ paid token may compete naturally
    # for #1.
    #
    # Lower-cap paid tokens stay protected in their
    # visibility zones.
    # --------------------------------------------------------

    if len(final_order) > 1:

        top_eligible = [
            item
            for item in final_order
            if (
                item["market_cap"]
                >= TOP_MC_THRESHOLD
                and item["has_activity"]
            )
        ]

        if top_eligible:

            top_eligible.sort(
                key=lambda item: (
                    item["market_score"],
                    item["final_score"],
                ),
                reverse=True,
            )

            strongest = top_eligible[0]

            current_index = final_order.index(
                strongest
            )

            if current_index != 0:

                final_order.pop(
                    current_index
                )

                final_order.insert(
                    0,
                    strongest,
                )

    return final_order


async def save_ranks(
    ranked_trends,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():

            for rank, item in enumerate(
                ranked_trends,
                start=1,
            ):
                await conn.execute(
                    """
                    UPDATE trends
                    SET
                        rank = $1,
                        updated_at = NOW()
                    WHERE id = $2
                      AND status = 'ACTIVE'
                    """,
                    rank,
                    item["id"],
                )


async def run_trending_cycle():
    inactivity_minutes = await get_setting(
        "trending_inactivity_minutes",
        DEFAULT_INACTIVITY_MINUTES,
    )

    trends = await get_active_trends()

    evaluated = []

    for trend in trends:

        market_data = await fetch_market_data(
            trend["chain"],
            trend["contract_address"],
        )

        if not market_data:
            # Temporary API failure must not immediately
            # remove an active listing.
            continue

        volume = safe_number(
            market_data.get(
                "volume_24h"
            )
        )

        liquidity = safe_number(
            market_data.get(
                "liquidity_usd"
            )
        )

        market_cap = safe_number(
            market_data.get(
                "market_cap"
            )
        )

        buys = safe_number(
            market_data.get(
                "buys_24h"
            )
        )

        price_change = safe_number(
            market_data.get(
                "price_change_24h"
            )
        )

        # ----------------------------------------------------
        # Recent activity score.
        # ----------------------------------------------------

        recent_activity = 100.0

        if trend["last_activity_at"]:

            elapsed = (
                datetime.now(
                    timezone.utc
                )
                - trend["last_activity_at"]
            ).total_seconds()

            recent_activity = max(
                0.0,
                100.0
                - (
                    elapsed
                    / 600.0
                    * 100.0
                ),
            )

        market_score = calculate_market_score(
            volume,
            liquidity,
            market_cap,
            buys,
            price_change,
            recent_activity,
        )

        previous_volume = safe_number(
            trend["last_volume_24h"]
        )

        has_activity = has_meaningful_activity(
            market_data,
            previous_volume,
        )

        paid_score = 0.0

        if trend["paid_promotion"]:

            paid_score = (
                calculate_paid_placement_score(
                    market_score,
                    market_cap,
                    has_activity,
                )
            )

        final_score = calculate_final_score(
            market_score,
            paid_score,
        )

        result = await update_trend_market_data(
            trend,
            market_data,
            market_score,
            final_score,
        )

        inactivity_started_at = (
            trend["inactivity_started_at"]
        )

        if (
            not has_activity
            and inactivity_started_at
        ):

            inactive_seconds = (
                datetime.now(
                    timezone.utc
                )
                - inactivity_started_at
            ).total_seconds()

            if (
                inactive_seconds
                >= inactivity_minutes * 60
            ):

                await remove_inactive_trend(
                    trend["id"]
                )

                print(
                    f"Removed inactive trend "
                    f"{trend['token_symbol']} "
                    f"after "
                    f"{inactivity_minutes} minutes."
                )

                continue

        evaluated.append(
            {
                "id": trend["id"],
                "paid_promotion": bool(
                    trend["paid_promotion"]
                ),
                "market_cap": market_cap,
                "market_score": market_score,
                "final_score": final_score,
                "has_activity": has_activity,
            }
        )

    ranked = await assign_ranks(
        evaluated
    )

    if ranked:
        await save_ranks(
            ranked
        )

    return ranked


async def trending_engine_worker():
    print(
        "Trending engine started."
    )

    while True:

        try:

            update_seconds = int(
                await get_setting(
                    "trending_update_seconds",
                    DEFAULT_UPDATE_SECONDS,
                )
            )

            await remove_expired_trends()

            ranked = await run_trending_cycle()

            if ranked:

                print(
                    "Trending rankings updated: "
                    + ", ".join(
                        f"#{index + 1}"
                        f"={item['id']}"
                        for index, item
                        in enumerate(ranked)
                    )
                )

        except asyncio.CancelledError:
            raise

        except Exception as exc:

            print(
                f"Trending engine error: {exc}"
            )

            update_seconds = (
                DEFAULT_UPDATE_SECONDS
            )

        await asyncio.sleep(
            update_seconds
        )
