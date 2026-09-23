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

REQUEST_TIMEOUT_SECONDS = 15


# ============================================================
# CACHE
# ============================================================

_PRICE_CACHE: dict[
    str,
    tuple[float, Any],
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
# WRAPPED NATIVE ASSETS
# ============================================================

WRAPPED_NATIVE_TOKENS = {
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


# ============================================================
# HTTP
# ============================================================

async def _request(
    url: str,
) -> Any:

    timeout = httpx.Timeout(
        REQUEST_TIMEOUT_SECONDS,
        connect=8.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "The-Block-Room/1.0"
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


def decimal_to_float(
    value: Optional[Decimal],
) -> Optional[float]:

    if value is None:
        return None

    try:
        return float(
            value
        )

    except (
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

    return str(
        address
    ).strip().lower()


# ============================================================
# CHAIN NORMALIZATION
# ============================================================

def normalize_chain(
    chain: Optional[str],
) -> Optional[str]:

    if not chain:
        return None

    value = str(
        chain
    ).strip().upper()

    aliases = {
        "BSC": "BNB",
        "BINANCE": "BNB",
        "BINANCE SMART CHAIN": "BNB",
        "BNB SMART CHAIN": "BNB",
        "ETHEREUM": "ETH",
        "ETH": "ETH",
        "SOL": "SOL",
        "SOLANA": "SOL",
        "BNB": "BNB",
    }

    return aliases.get(
        value,
        value
        if value in DEXSCREENER_CHAIN_IDS
        else None,
    )


# ============================================================
# CACHE
# ============================================================

def _cache_get(
    key: str,
):

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
    value: Any,
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

    normalized_chain = normalize_chain(
        chain
    )

    chain_id = (
        DEXSCREENER_CHAIN_IDS.get(
            normalized_chain
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
        "pairs:"
        f"{chain_id}:"
        f"{normalized_address}"
    )

    cached = _cache_get(
        cache_key
    )

    if cached is not None:

        if isinstance(
            cached,
            list,
        ):
            return cached

        return []

    url = (
        f"{DEXSCREENER_BASE_URL}"
        f"/token-pairs/v1/"
        f"{chain_id}/"
        f"{token_address}"
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

        _cache_set(
            cache_key,
            data,
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

        transactions = (
            pair.get(
                "txns"
            )
            or {}
        )

        h24 = (
            transactions.get(
                "h24"
            )
            or {}
        )

        buys = int(
            _decimal(
                h24.get(
                    "buys"
                )
            )
            or 0
        )

        sells = int(
            _decimal(
                h24.get(
                    "sells"
                )
            )
            or 0
        )

        candidates.append(
            (
                liquidity_usd,
                volume_24h,
                buys + sells,
                pair,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2],
        ),
        reverse=True,
    )

    return candidates[0][3]


# ============================================================
# COMPATIBILITY ALIAS
# ============================================================

def choose_best_pair(
    pairs,
):
    if not pairs:
        return None

    candidates = []

    for pair in pairs:

        if not isinstance(
            pair,
            dict,
        ):
            continue

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

        txns = (
            pair.get(
                "txns"
            )
            or {}
        )

        h24 = (
            txns.get(
                "h24"
            )
            or {}
        )

        liquidity_usd = _decimal(
            liquidity.get(
                "usd"
            )
        ) or Decimal("0")

        volume_24h = _decimal(
            volume.get(
                "h24"
            )
        ) or Decimal("0")

        activity = (
            int(
                _decimal(
                    h24.get(
                        "buys"
                    )
                )
                or 0
            )
            +
            int(
                _decimal(
                    h24.get(
                        "sells"
                    )
                )
                or 0
            )
        )

        candidates.append(
            (
                liquidity_usd,
                volume_24h,
                activity,
                pair,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2],
        ),
        reverse=True,
    )

    return candidates[0][3]


# ============================================================
# PARSE PAIR
# ============================================================

def parse_pair(
    pair,
) -> Optional[dict[str, Any]]:

    if not pair:
        return None

    if not isinstance(
        pair,
        dict,
    ):
        return None

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

    price_change = (
        pair.get(
            "priceChange"
        )
        or {}
    )

    txns = (
        pair.get(
            "txns"
        )
        or {}
    )

    h24 = (
        txns.get(
            "h24"
        )
        or {}
    )

    h1 = (
        txns.get(
            "h1"
        )
        or {}
    )

    h6 = (
        txns.get(
            "h6"
        )
        or {}
    )

    m5 = (
        txns.get(
            "m5"
        )
        or {}
    )

    return {
        "name": base_token.get(
            "name"
        ),

        "symbol": base_token.get(
            "symbol"
        ),

        "address": base_token.get(
            "address"
        ),

        "pair_address": pair.get(
            "pairAddress"
        ),

        "dex": pair.get(
            "dexId"
        ),

        "price_usd": decimal_to_float(
            _decimal(
                pair.get(
                    "priceUsd"
                )
            )
        ),

        "liquidity_usd": decimal_to_float(
            _decimal(
                liquidity.get(
                    "usd"
                )
            )
        ) or 0.0,

        "volume_24h": decimal_to_float(
            _decimal(
                volume.get(
                    "h24"
                )
            )
        ) or 0.0,

        "market_cap": decimal_to_float(
            _decimal(
                pair.get(
                    "marketCap"
                )
            )
        ) or 0.0,

        "fdv": decimal_to_float(
            _decimal(
                pair.get(
                    "fdv"
                )
            )
        ) or 0.0,

        "price_change_5m": decimal_to_float(
            _decimal(
                price_change.get(
                    "m5"
                )
            )
        ) or 0.0,

        "price_change_1h": decimal_to_float(
            _decimal(
                price_change.get(
                    "h1"
                )
            )
        ) or 0.0,

        "price_change_6h": decimal_to_float(
            _decimal(
                price_change.get(
                    "h6"
                )
            )
        ) or 0.0,

        "price_change_24h": decimal_to_float(
            _decimal(
                price_change.get(
                    "h24"
                )
            )
        ) or 0.0,

        "buys_5m": int(
            _decimal(
                m5.get(
                    "buys"
                )
            )
            or 0
        ),

        "sells_5m": int(
            _decimal(
                m5.get(
                    "sells"
                )
            )
            or 0
        ),

        "buys_1h": int(
            _decimal(
                h1.get(
                    "buys"
                )
            )
            or 0
        ),

        "sells_1h": int(
            _decimal(
                h1.get(
                    "sells"
                )
            )
            or 0
        ),

        "buys_6h": int(
            _decimal(
                m6.get(
                    "buys"
                )
            )
            or 0
        ) if False else int(
            _decimal(
                h6.get(
                    "buys"
                )
            )
            or 0
        ),

        "sells_6h": int(
            _decimal(
                h6.get(
                    "sells"
                )
            )
            or 0
        ),

        "buys_24h": int(
            _decimal(
                h24.get(
                    "buys"
                )
            )
            or 0
        ),

        "sells_24h": int(
            _decimal(
                h24.get(
                    "sells"
                )
            )
            or 0
        ),

        "pair_created_at": pair.get(
            "pairCreatedAt"
        ),

        "url": pair.get(
            "url"
        ),

        "dex_url": pair.get(
            "url"
        ),

        "quote_token": quote_token.get(
            "address"
        ),

        "quote_symbol": quote_token.get(
            "symbol"
        ),

        "quote_name": quote_token.get(
            "name"
        ),

        "chain_id": pair.get(
            "chainId"
        ),

        "pair": pair,
    }


# ============================================================
# TOKEN MARKET DATA
# ============================================================

async def get_token_market_data(
    chain: str,
    token_address: str,
) -> Optional[dict[str, Any]]:

    normalized_chain = normalize_chain(
        chain
    )

    pairs = await get_token_pairs(
        chain=normalized_chain,
        token_address=token_address,
    )

    pair = select_best_pair(
        pairs=pairs,
        token_address=token_address,
    )

    if not pair:
        return None

    parsed = parse_pair(
        pair
    )

    if not parsed:
        return None

    return {
        "chain": normalized_chain,

        "token_address": token_address,

        "token_name": parsed.get(
            "name"
        ),

        "token_symbol": parsed.get(
            "symbol"
        ),

        "price_usd": _decimal(
            parsed.get(
                "price_usd"
            )
        ),

        "market_cap_usd": _decimal(
            parsed.get(
                "market_cap"
            )
        ),

        "fdv_usd": _decimal(
            parsed.get(
                "fdv"
            )
        ),

        "liquidity_usd": _decimal(
            parsed.get(
                "liquidity_usd"
            )
        ),

        "volume_24h_usd": _decimal(
            parsed.get(
                "volume_24h"
            )
        ),

        "price_change_5m": _decimal(
            parsed.get(
                "price_change_5m"
            )
        ),

        "price_change_1h": _decimal(
            parsed.get(
                "price_change_1h"
            )
        ),

        "price_change_6h": _decimal(
            parsed.get(
                "price_change_6h"
            )
        ),

        "price_change_24h": _decimal(
            parsed.get(
                "price_change_24h"
            )
        ),

        "buys_24h": parsed.get(
            "buys_24h",
            0,
        ),

        "sells_24h": parsed.get(
            "sells_24h",
            0,
        ),

        "pair_address": parsed.get(
            "pair_address"
        ),

        "dex_id": parsed.get(
            "dex"
        ),

        "dex_url": parsed.get(
            "dex_url"
        ),

        "pair": pair,
    }


# ============================================================
# NATIVE ASSET PRICES
# ============================================================

async def get_native_price_usd(
    chain: str,
) -> Optional[Decimal]:

    normalized_chain = normalize_chain(
        chain
    )

    token_address = (
        WRAPPED_NATIVE_TOKENS.get(
            normalized_chain
        )
    )

    if not token_address:
        return None

    cache_key = (
        "native-price:"
        f"{normalized_chain}"
    )

    cached = _cache_get(
        cache_key
    )

    if cached is not None:

        return _decimal(
            cached
        )

    pairs = await get_token_pairs(
        chain=normalized_chain,
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

    price = _decimal(
        best_pair.get(
            "priceUsd"
        )
    )

    if price is not None:

        _cache_set(
            cache_key,
            str(price),
        )

    return price


# ============================================================
# NATIVE AMOUNT TO USD
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
# GENERIC QUOTE TO USD
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

    if normalized_type in {
        "USD",
        "STABLE",
        "STABLECOIN",
    }:

        return amount

    if normalized_type in {
        "NATIVE",
        "BNB",
        "ETH",
        "SOL",
    }:

        return await native_amount_to_usd(
            chain=chain,
            amount=amount,
        )

    return None


# ============================================================
# SOL PRICE
# ============================================================

async def get_sol_price_usd():

    return await get_native_price_usd(
        "SOL"
    )


# ============================================================
# BNB PRICE
# ============================================================

async def get_bnb_price_usd():

    return await get_native_price_usd(
        "BNB"
    )


# ============================================================
# ETH PRICE
# ============================================================

async def get_eth_price_usd():

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
        "chain": normalize_chain(
            chain
        ),

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

        "buys_24h": 0,

        "sells_24h": 0,

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

            for (
                key,
                cached,
            ) in list(
                _PRICE_CACHE.items()
            ):

                created_at, _value = (
                    cached
                )

                if (
                    now
                    - created_at
                    > CACHE_SECONDS * 3
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
