from __future__ import annotations

import asyncio
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx


# ============================================================
# DEXSCREENER
# ============================================================

DEXSCREENER_BASE_URL = (
    "https://api.dexscreener.com"
)


# ============================================================
# CACHE
# ============================================================

_PRICE_CACHE: dict[
    str,
    tuple[float, dict[str, Any]],
] = {}

CACHE_SECONDS = 20


# ============================================================
# CHAIN MAPPING
# ============================================================

DEXSCREENER_CHAIN_IDS = {
    "BNB": "bsc",
    "ETH": "ethereum",
    "SOL": "solana",
}


# ============================================================
# HTTP CLIENT
# ============================================================

async def _request(
    url: str,
) -> Any:

    timeout = httpx.Timeout(
        15.0,
        connect=8.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "The-Block-Room-BuyBot/1.0"
            ),
        },
    ) as client:

        response = await client.get(
            url
        )

        response.raise_for_status()

        return response.json()


# ============================================================
# DECIMAL HELPERS
# ============================================================

def _decimal(
    value: Any,
) -> Optional[Decimal]:

    if value is None:
        return None

    try:
        return Decimal(
            str(value)
        )

    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        return None


# ============================================================
# ADDRESS NORMALIZATION
# ============================================================

def normalize_address(
    address: Optional[str],
) -> Optional[str]:

    if not address:
        return None

    return str(address).strip().lower()


# ============================================================
# CACHE
# ============================================================

def _cache_get(
    key: str,
) -> Optional[dict[str, Any]]:

    cached = _PRICE_CACHE.get(
        key
    )

    if not cached:
        return None

    created_at, value = cached

    if (
        time.monotonic()
        - created_at
        > CACHE_SECONDS
    ):
        _PRICE_CACHE.pop(
            key,
            None,
        )

        return None

    return value


def _cache_set(
    key: str,
    value: dict[str, Any],
):
    _PRICE_CACHE[key] = (
        time.monotonic(),
        value,
    )


# ============================================================
# FIND TOKEN PAIRS
# ============================================================

async def get_token_pairs(
    chain: str,
    token_address: str,
) -> list[dict[str, Any]]:

    chain_id = (
        DEXSCREENER_CHAIN_IDS.get(
            chain
        )
    )

    if not chain_id:
        return []

    normalized_address = (
        normalize_address(
            token_address
        )
    )

    if not normalized_address:
        return []

    cache_key = (
        f"pairs:"
        f"{chain_id}:"
        f"{normalized_address}"
    )

    cached = _cache_get(
        cache_key
    )

    if cached is not None:
        return cached.get(
            "pairs",
            [],
        )

    url = (
        f"{DEXSCREENER_BASE_URL}"
        f"/token-pairs/v1/"
        f"{chain_id}/"
        f"{normalized_address}"
    )

    try:

        data = await _request(
            url
        )

        if not isinstance(
            data,
            list,
        ):
            data = []

        result = {
            "pairs": data
        }

        _cache_set(
            cache_key,
            result,
        )

        return data

    except Exception as exc:

        print(
            "DexScreener token-pairs "
            f"request failed: {exc}"
        )

        return []


# ============================================================
# SELECT BEST PAIR
# ============================================================

def select_best_pair(
    pairs: list[dict[str, Any]],
    token_address: str,
) -> Optional[dict[str, Any]]:

    normalized_address = (
        normalize_address(
            token_address
        )
    )

    if not normalized_address:
        return None

    candidates = []

    for pair in pairs:

        if not isinstance(
            pair,
            dict,
        ):
            continue

        base_token = pair.get(
            "baseToken"
        ) or {}

        quote_token = pair.get(
            "quoteToken"
        ) or {}

        base_address = normalize_address(
            base_token.get(
                "address"
            )
        )

        quote_address = normalize_address(
            quote_token.get(
                "address"
            )
        )

        if (
            base_address
            != normalized_address
            and quote_address
            != normalized_address
        ):
            continue

        liquidity = (
            pair.get(
                "liquidity"
            )
            or {}
        )

        liquidity_usd = (
            _decimal(
                liquidity.get(
                    "usd"
                )
            )
            or Decimal("0")
        )

        volume = (
            pair.get(
                "volume"
            )
            or {}
        )

        volume_24h = (
            _decimal(
                volume.get(
                    "h24"
                )
            )
            or Decimal("0")
        )

        candidates.append(
            (
                liquidity_usd,
                volume_24h,
                pair,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
        ),
        reverse=True,
    )

    return candidates[0][2]


# ============================================================
# TOKEN MARKET DATA
# ============================================================

async def get_token_market_data(
    chain: str,
    token_address: str,
) -> Optional[dict[str, Any]]:

    pairs = await get_token_pairs(
        chain=chain,
        token_address=token_address,
    )

    pair = select_best_pair(
        pairs=pairs,
        token_address=token_address,
    )

    if not pair:
        return None

    price_usd = _decimal(
        pair.get(
            "priceUsd"
        )
    )

    market_cap = _decimal(
        pair.get(
            "marketCap"
        )
    )

    fdv = _decimal(
        pair.get(
            "fdv"
        )
    )

    liquidity = (
        pair.get(
            "liquidity"
        )
        or {}
    )

    liquidity_usd = _decimal(
        liquidity.get(
            "usd"
        )
    )

    volume = (
        pair.get(
            "volume"
        )
        or {}
    )

    volume_24h = _decimal(
        volume.get(
            "h24"
        )
    )

    price_change = (
        pair.get(
            "priceChange"
        )
        or {}
    )

    price_change_5m = _decimal(
        price_change.get(
            "m5"
        )
    )

    price_change_1h = _decimal(
        price_change.get(
            "h1"
        )
    )

    price_change_6h = _decimal(
        price_change.get(
            "h6"
        )
    )

    price_change_24h = _decimal(
        price_change.get(
            "h24"
        )
    )

    base_token = (
        pair.get(
            "baseToken"
        )
        or {}
    )

    quote_token = (
        pair.get(
            "quoteToken"
        )
        or {}
    )

    result = {
        "chain": chain,
        "token_address": token_address,
        "token_name": base_token.get(
            "name"
        ),
        "token_symbol": base_token.get(
            "symbol"
        ),
        "price_usd": price_usd,
        "market_cap_usd": market_cap,
        "fdv_usd": fdv,
        "liquidity_usd": liquidity_usd,
        "volume_24h_usd": volume_24h,
        "price_change_5m": price_change_5m,
        "price_change_1h": price_change_1h,
        "price_change_6h": price_change_6h,
        "price_change_24h": price_change_24h,
        "pair_address": pair.get(
            "pairAddress"
        ),
        "dex_id": pair.get(
            "dexId"
        ),
        "dex_url": pair.get(
            "url"
        ),
        "pair": pair,
    }

    return result


# ============================================================
# NATIVE ASSET PRICES
# ============================================================

async def get_native_price_usd(
    chain: str,
) -> Optional[Decimal]:

    mapping = {
        "BNB": (
            "0x"
            "bb4cdb9cbd36b01bd1cbaeb"
            "f2de08d9173bc095c"
        ),
        "ETH": (
            "0x"
            "c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
        ),
        "SOL": (
            "So11111111111111111111111111111111111111112"
        ),
    }

    token_address = mapping.get(
        chain
    )

    if not token_address:
        return None

    pairs = await get_token_pairs(
        chain=chain,
        token_address=token_address,
    )

    if not pairs:
        return None

    best_pair = select_best_pair(
        pairs=pairs,
        token_address=token_address,
    )

    if not best_pair:
        return None

    return _decimal(
        best_pair.get(
            "priceUsd"
        )
    )


# ============================================================
# CONVERT NATIVE AMOUNT TO USD
# ============================================================

async def native_amount_to_usd(
    chain: str,
    amount: Decimal,
) -> Optional[Decimal]:

    if amount is None:
        return None

    price = await get_native_price_usd(
        chain
    )

    if price is None:
        return None

    return (
        amount * price
    )


# ============================================================
# GENERIC AMOUNT TO USD
# ============================================================

async def quote_amount_to_usd(
    *,
    chain: str,
    quote_type: str,
    amount: Decimal,
) -> Optional[Decimal]:

    if amount is None:
        return None

    normalized_type = (
        str(
            quote_type or ""
        )
        .strip()
        .upper()
    )

    if normalized_type == "USD":
        return amount

    if normalized_type == "NATIVE":
        return await native_amount_to_usd(
            chain=chain,
            amount=amount,
        )

    return None


# ============================================================
# SOL PRICE
# ============================================================

async def get_sol_price_usd() -> Optional[Decimal]:

    return await get_native_price_usd(
        "SOL"
    )


# ============================================================
# BNB PRICE
# ============================================================

async def get_bnb_price_usd() -> Optional[Decimal]:

    return await get_native_price_usd(
        "BNB"
    )


# ============================================================
# ETH PRICE
# ============================================================

async def get_eth_price_usd() -> Optional[Decimal]:

    return await get_native_price_usd(
        "ETH"
    )


# ============================================================
# SAFE MARKET DATA
# ============================================================

async def get_market_data_safe(
    chain: str,
    token_address: str,
) -> dict[str, Any]:

    try:

        data = await get_token_market_data(
            chain=chain,
            token_address=token_address,
        )

        if data:
            return data

    except Exception as exc:

        print(
            "Market data error: "
            f"{exc}"
        )

    return {
        "chain": chain,
        "token_address": token_address,
        "token_name": None,
        "token_symbol": None,
        "price_usd": None,
        "market_cap_usd": None,
        "fdv_usd": None,
        "liquidity_usd": None,
        "volume_24h_usd": None,
        "price_change_5m": None,
        "price_change_1h": None,
        "price_change_6h": None,
        "price_change_24h": None,
        "pair_address": None,
        "dex_id": None,
        "dex_url": None,
        "pair": None,
    }


# ============================================================
# CACHE CLEANUP
# ============================================================

async def market_data_cache_cleanup():

    while True:

        try:

            now = time.monotonic()

            expired_keys = []

            for key, (
                created_at,
                _value,
            ) in list(
                _PRICE_CACHE.items()
            ):

                if (
                    now - created_at
                    > CACHE_SECONDS
                    * 3
                ):
                    expired_keys.append(
                        key
                    )

            for key in expired_keys:

                _PRICE_CACHE.pop(
                    key,
                    None,
                )

        except Exception as exc:

            print(
                "Market data cache cleanup "
                f"error: {exc}"
            )

        await asyncio.sleep(
            CACHE_SECONDS
        )
