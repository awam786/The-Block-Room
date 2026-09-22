from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from services.buybot_holder import (
    is_first_observed_holder,
)


DEX_BASE_URL = (
    "https://api.dexscreener.com"
)


CHAIN_MAP = {
    "bnb": "bsc",
    "ethereum": "ethereum",
    "solana": "solana",
    "robinhood": "robinhood",
}


STABLECOINS = {
    "USDT",
    "USDC",
    "DAI",
    "BUSD",
    "FDUSD",
}


def decimal_value(
    value,
    default: Decimal = Decimal("0"),
) -> Decimal:
    if value is None:
        return default

    try:
        return Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return default


async def get_token_pairs(
    chain: str,
    token_address: str,
):
    dex_chain = CHAIN_MAP.get(
        chain.lower()
    )

    if not dex_chain:
        return []

    url = (
        f"{DEX_BASE_URL}/latest/dex/tokens/"
        f"{token_address}"
    )

    try:
        async with httpx.AsyncClient(
            timeout=15
        ) as client:
            response = await client.get(
                url
            )

            response.raise_for_status()

            data = response.json()

    except (
        httpx.HTTPError,
        ValueError,
    ):
        return []

    pairs = data.get(
        "pairs"
    ) or []

    return [
        pair
        for pair in pairs
        if str(
            pair.get("chainId", "")
        ).lower()
        == dex_chain
    ]


def choose_best_pair(
    pairs: list,
):
    if not pairs:
        return None

    def liquidity_value(
        pair
    ):
        liquidity = (
            pair.get(
                "liquidity"
            )
            or {}
        )

        return float(
            liquidity.get(
                "usd"
            )
            or 0
        )

    return max(
        pairs,
        key=liquidity_value,
    )


def extract_market_data(
    pair: Optional[dict],
):
    if not pair:
        return {
            "price_usd": Decimal("0"),
            "market_cap_usd": Decimal("0"),
            "fdv_usd": Decimal("0"),
            "liquidity_usd": Decimal("0"),
            "volume_24h_usd": Decimal("0"),
            "pair_url": None,
            "dex_url": None,
        }

    liquidity = (
        pair.get(
            "liquidity"
        )
        or {}
    )

    volume = (
        pair.get(
            "volume"
        )
        or {}
    )

    return {
        "price_usd": decimal_value(
            pair.get(
                "priceUsd"
            )
        ),
        "market_cap_usd": decimal_value(
            pair.get(
                "marketCap"
            )
        ),
        "fdv_usd": decimal_value(
            pair.get(
                "fdv"
            )
        ),
        "liquidity_usd": decimal_value(
            liquidity.get(
                "usd"
            )
        ),
        "volume_24h_usd": decimal_value(
            volume.get(
                "h24"
            )
        ),
        "pair_url": pair.get(
            "url"
        ),
        "dex_url": pair.get(
            "url"
        ),
    }


async def get_market_data(
    chain: str,
    token_address: str,
):
    pairs = await get_token_pairs(
        chain,
        token_address,
    )

    pair = choose_best_pair(
        pairs
    )

    return extract_market_data(
        pair
    )


async def get_quote_price_usd(
    chain: str,
    quote_symbol: str,
    quote_token_address: Optional[str] = None,
):
    symbol = (
        quote_symbol
        or ""
    ).upper().strip()

    if symbol in STABLECOINS:
        return Decimal("1")

    if not quote_token_address:
        return Decimal("0")

    data = await get_market_data(
        chain,
        quote_token_address,
    )

    return data[
        "price_usd"
    ]


async def calculate_spent_usd(
    chain: str,
    native_amount: Decimal,
    native_symbol: str,
    quote_token_address: Optional[str] = None,
):
    if native_amount <= 0:
        return Decimal("0")

    symbol = (
        native_symbol
        or ""
    ).upper().strip()

    if symbol in STABLECOINS:
        return native_amount

    price = await get_quote_price_usd(
        chain,
        symbol,
        quote_token_address,
    )

    if price <= 0:
        return Decimal("0")

    return (
        native_amount
        * price
    )


async def enrich_buy_event(
    event,
):
    try:
        market_data = (
            await get_market_data(
                event.chain,
                event.token_address,
            )
        )

        event.market_cap_usd = (
            market_data[
                "market_cap_usd"
            ]
        )

        if not event.dex_url:
            event.dex_url = (
                market_data[
                    "dex_url"
                ]
            )

        if (
            event.spent_amount_usd
            <= 0
        ):
            event.spent_amount_usd = (
                await calculate_spent_usd(
                    event.chain,
                    event.spent_native_amount,
                    event.spent_native_symbol,
                )
            )

    except Exception as exc:
        print(
            "BuyBot market enrichment error "
            f"chain={event.chain} "
            f"token={event.token_address}: "
            f"{exc}"
        )

    return event


async def enrich_with_holder_status(
    event,
):
    try:
        event.is_new_holder = (
            await is_first_observed_holder(
                event.chain,
                event.token_address,
                event.buyer_address,
            )
        )

    except Exception as exc:
        print(
            "BuyBot holder detection error "
            f"chain={event.chain} "
            f"token={event.token_address}: "
            f"{exc}"
        )

        event.is_new_holder = False

    return event


async def enrich_event(
    event,
):
    event = await enrich_buy_event(
        event
    )

    event = await enrich_with_holder_status(
        event
    )

    return event
