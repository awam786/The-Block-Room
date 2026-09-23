import time
from typing import Any, Optional

import httpx


DEX_SCREENER_BASE_URL = (
    "https://api.dexscreener.com"
)

TOKEN_PAIRS_ENDPOINT = (
    DEX_SCREENER_BASE_URL
    + "/token-pairs/v1/{chain_id}/{token_address}"
)


# ============================================================
# CHAIN CONFIGURATION
# ============================================================

CHAIN_IDS = {
    "bnb": "bsc",
    "ethereum": "ethereum",
    "solana": "solana",
    "robinhood": "robinhood",
}


# Wrapped native assets used when a pair is quoted
# against the chain's native asset.

WRAPPED_NATIVE = {
    "bnb": (
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
    ),
    "ethereum": (
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    ),
    "solana": (
        "So11111111111111111111111111111111111111112"
    ),
    "robinhood": (
        "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
    ),
}


NATIVE_SYMBOLS = {
    "bnb": "BNB",
    "ethereum": "ETH",
    "solana": "SOL",
    "robinhood": "ETH",
}


# ============================================================
# CACHE
# ============================================================

_CACHE: dict[
    tuple[str, str],
    tuple[float, Any],
] = {}

CACHE_SECONDS = 10


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_chain(
    chain: str,
) -> str:
    value = (
        str(chain or "")
        .strip()
        .lower()
    )

    aliases = {
        "bsc": "bnb",
        "binance": "bnb",
        "binance-smart-chain": "bnb",
        "eth": "ethereum",
        "sol": "solana",
        "rh": "robinhood",
        "robinhood-chain": "robinhood",
    }

    return aliases.get(
        value,
        value,
    )


def normalize_address(
    address: Optional[str],
) -> str:
    if not address:
        return ""

    return address.strip()


def normalize_evm_address(
    address: Optional[str],
) -> str:
    return normalize_address(
        address
    ).lower()


# ============================================================
# SAFE NUMERIC HELPERS
# ============================================================

def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        if value is None:
            return default

        return float(value)

    except (
        TypeError,
        ValueError,
    ):
        return default


def safe_int(
    value: Any,
    default: int = 0,
) -> int:
    try:
        if value is None:
            return default

        return int(value)

    except (
        TypeError,
        ValueError,
    ):
        return default


# ============================================================
# DEX SCREENER REQUEST
# ============================================================

async def get_token_pairs(
    chain: str,
    token_address: str,
):
    chain = normalize_chain(chain)
    token_address = normalize_address(
        token_address
    )

    chain_id = CHAIN_IDS.get(chain)

    if not chain_id:
        return []

    if not token_address:
        return []

    cache_key = (
        chain,
        token_address.lower(),
    )

    now = time.monotonic()

    cached = _CACHE.get(
        cache_key
    )

    if cached:
        cached_at, cached_value = cached

        if (
            now - cached_at
            < CACHE_SECONDS
        ):
            return cached_value

    url = TOKEN_PAIRS_ENDPOINT.format(
        chain_id=chain_id,
        token_address=token_address,
    )

    try:
        async with httpx.AsyncClient(
            timeout=15
        ) as client:
            response = await client.get(
                url
            )

            if response.status_code == 404:
                pairs = []

            else:
                response.raise_for_status()

                data = response.json()

                if isinstance(
                    data,
                    dict,
                ):
                    pairs = data.get(
                        "pairs",
                        [],
                    )

                elif isinstance(
                    data,
                    list,
                ):
                    pairs = data

                else:
                    pairs = []

    except Exception as exc:
        print(
            "DEX Screener market-data "
            f"error chain={chain} "
            f"token={token_address}: "
            f"{exc}"
        )

        return []

    if not isinstance(
        pairs,
        list,
    ):
        pairs = []

    _CACHE[cache_key] = (
        now,
        pairs,
    )

    return pairs


# ============================================================
# BEST PAIR
# ============================================================

def choose_best_pair(
    pairs: list,
) -> Optional[dict]:
    if not pairs:
        return None

    valid_pairs = []

    for pair in pairs:
        if not isinstance(
            pair,
            dict,
        ):
            continue

        liquidity = (
            pair.get("liquidity")
            or {}
        )

        volume = (
            pair.get("volume")
            or {}
        )

        txns = (
            pair.get("txns")
            or {}
        )

        liquidity_usd = safe_float(
            liquidity.get("usd")
        )

        volume_24h = safe_float(
            volume.get("h24")
        )

        h24_txns = (
            txns.get("h24")
            or {}
        )

        buys = safe_int(
            h24_txns.get("buys")
        )

        sells = safe_int(
            h24_txns.get("sells")
        )

        activity = buys + sells

        valid_pairs.append(
            (
                liquidity_usd,
                volume_24h,
                activity,
                pair,
            )
        )

    if not valid_pairs:
        return None

    valid_pairs.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2],
        ),
        reverse=True,
    )

    return valid_pairs[0][3]


# ============================================================
# PARSE PAIR
# ============================================================

def parse_pair(
    pair: dict,
    chain: Optional[str] = None,
) -> dict:
    chain = normalize_chain(
        chain
        or pair.get("chainId")
        or ""
    )

    base = (
        pair.get("baseToken")
        or {}
    )

    quote = (
        pair.get("quoteToken")
        or {}
    )

    liquidity = (
        pair.get("liquidity")
        or {}
    )

    volume = (
        pair.get("volume")
        or {}
    )

    price_change = (
        pair.get("priceChange")
        or {}
    )

    txns = (
        pair.get("txns")
        or {}
    )

    def tx_values(
        period: str,
    ):
        values = (
            txns.get(period)
            or {}
        )

        return {
            "buys": safe_int(
                values.get("buys")
            ),
            "sells": safe_int(
                values.get("sells")
            ),
        }

    tx_5m = tx_values("m5")
    tx_1h = tx_values("h1")
    tx_6h = tx_values("h6")
    tx_24h = tx_values("h24")

    return {
        "name": base.get(
            "name"
        ) or "Unknown",
        "symbol": base.get(
            "symbol"
        ) or "UNKNOWN",
        "address": base.get(
            "address"
        ),

        "pair_address": pair.get(
            "pairAddress"
        ),

        "dex": pair.get(
            "dexId"
        ),

        "chain": chain,

        "url": pair.get(
            "url"
        ),

        "dex_url": pair.get(
            "url"
        ),

        "price_usd": safe_float(
            pair.get(
                "priceUsd"
            )
        ),

        "price_native": safe_float(
            pair.get(
                "priceNative"
            )
        ),

        "liquidity_usd": safe_float(
            liquidity.get("usd")
        ),

        "liquidity_base": safe_float(
            liquidity.get("base")
        ),

        "liquidity_quote": safe_float(
            liquidity.get("quote")
        ),

        "market_cap": safe_float(
            pair.get("marketCap")
        ),

        "fdv": safe_float(
            pair.get("fdv")
        ),

        "volume_5m": safe_float(
            volume.get("m5")
        ),

        "volume_1h": safe_float(
            volume.get("h1")
        ),

        "volume_6h": safe_float(
            volume.get("h6")
        ),

        "volume_24h": safe_float(
            volume.get("h24")
        ),

        "price_change_5m": safe_float(
            price_change.get("m5")
        ),

        "price_change_1h": safe_float(
            price_change.get("h1")
        ),

        "price_change_6h": safe_float(
            price_change.get("h6")
        ),

        "price_change_24h": safe_float(
            price_change.get("h24")
        ),

        "buys_5m": tx_5m["buys"],
        "sells_5m": tx_5m["sells"],

        "buys_1h": tx_1h["buys"],
        "sells_1h": tx_1h["sells"],

        "buys_6h": tx_6h["buys"],
        "sells_6h": tx_6h["sells"],

        "buys_24h": tx_24h["buys"],
        "sells_24h": tx_24h["sells"],

        "pair_created_at": pair.get(
            "pairCreatedAt"
        ),

        "quote_address": quote.get(
            "address"
        ),

        "quote_name": quote.get(
            "name"
        ),

        "quote_symbol": quote.get(
            "symbol"
        ),

        "labels": pair.get(
            "labels"
        ) or [],

        "image_url": (
            pair.get("info", {})
            .get("imageUrl")
        ),
    }


# ============================================================
# BEST TOKEN MARKET DATA
# ============================================================

async def get_best_token_pair(
    chain: str,
    token_address: str,
) -> Optional[dict]:
    chain = normalize_chain(chain)

    pairs = await get_token_pairs(
        chain,
        token_address,
    )

    best = choose_best_pair(
        pairs
    )

    if not best:
        return None

    return parse_pair(
        best,
        chain,
    )


# ============================================================
# COMPATIBILITY FUNCTION
# ============================================================

async def get_token_market_data(
    chain: str,
    token_address: str,
) -> Optional[dict]:
    return await get_best_token_pair(
        chain,
        token_address,
    )


# ============================================================
# SIMPLE PRICE HELPERS
# ============================================================

async def get_token_price_usd(
    chain: str,
    token_address: str,
) -> float:
    data = await get_best_token_pair(
        chain,
        token_address,
    )

    if not data:
        return 0.0

    return safe_float(
        data.get("price_usd")
    )


async def get_token_market_cap(
    chain: str,
    token_address: str,
) -> float:
    data = await get_best_token_pair(
        chain,
        token_address,
    )

    if not data:
        return 0.0

    return safe_float(
        data.get("market_cap")
    )


async def get_token_volume_24h(
    chain: str,
    token_address: str,
) -> float:
    data = await get_best_token_pair(
        chain,
        token_address,
    )

    if not data:
        return 0.0

    return safe_float(
        data.get("volume_24h")
    )


async def get_token_liquidity_usd(
    chain: str,
    token_address: str,
) -> float:
    data = await get_best_token_pair(
        chain,
        token_address,
    )

    if not data:
        return 0.0

    return safe_float(
        data.get("liquidity_usd")
    )


# ============================================================
# WRAPPED NATIVE HELPERS
# ============================================================

def get_wrapped_native_address(
    chain: str,
) -> str:
    chain = normalize_chain(
        chain
    )

    return WRAPPED_NATIVE.get(
        chain,
        "",
    )


def get_native_symbol(
    chain: str,
) -> str:
    chain = normalize_chain(
        chain
    )

    return NATIVE_SYMBOLS.get(
        chain,
        "",
    )


# ============================================================
# SAFE MARKET DATA
# ============================================================

async def get_market_data_safe(
    chain: str,
    token_address: str,
) -> dict:
    try:
        data = await get_best_token_pair(
            chain,
            token_address,
        )

        if data:
            return data

    except Exception as exc:
        print(
            "Market data error "
            f"chain={chain} "
            f"token={token_address}: "
            f"{exc}"
        )

    return {
        "name": "Unknown",
        "symbol": "UNKNOWN",
        "address": token_address,
        "pair_address": None,
        "dex": None,
        "chain": normalize_chain(chain),
        "url": None,
        "dex_url": None,
        "price_usd": 0.0,
        "price_native": 0.0,
        "liquidity_usd": 0.0,
        "liquidity_base": 0.0,
        "liquidity_quote": 0.0,
        "market_cap": 0.0,
        "fdv": 0.0,
        "volume_5m": 0.0,
        "volume_1h": 0.0,
        "volume_6h": 0.0,
        "volume_24h": 0.0,
        "price_change_5m": 0.0,
        "price_change_1h": 0.0,
        "price_change_6h": 0.0,
        "price_change_24h": 0.0,
        "buys_5m": 0,
        "sells_5m": 0,
        "buys_1h": 0,
        "sells_1h": 0,
        "buys_6h": 0,
        "sells_6h": 0,
        "buys_24h": 0,
        "sells_24h": 0,
        "pair_created_at": None,
        "quote_address": None,
        "quote_name": None,
        "quote_symbol": None,
        "labels": [],
        "image_url": None,
    }


# ============================================================
# NATIVE ASSET USD PRICE
# ============================================================

async def get_native_price_usd(
    chain: str,
) -> float:
    """
    Gets the approximate USD price of the native
    asset by querying a well-known wrapped-native
    token pair.
    """

    chain = normalize_chain(
        chain
    )

    wrapped = get_wrapped_native_address(
        chain
    )

    if not wrapped:
        return 0.0

    data = await get_best_token_pair(
        chain,
        wrapped,
    )

    if not data:
        return 0.0

    return safe_float(
        data.get("price_usd")
    )


async def get_bnb_price_usd() -> float:
    return await get_native_price_usd(
        "bnb"
    )


async def get_eth_price_usd() -> float:
    return await get_native_price_usd(
        "ethereum"
    )


async def get_sol_price_usd() -> float:
    return await get_native_price_usd(
        "solana"
    )


async def get_robinhood_eth_price_usd() -> float:
    return await get_native_price_usd(
        "robinhood"
    )


# ============================================================
# NATIVE AMOUNT CONVERSION
# ============================================================

async def native_amount_to_usd(
    chain: str,
    amount: float,
) -> float:
    price = await get_native_price_usd(
        chain
    )

    return (
        safe_float(amount)
        * price
    )


async def quote_amount_to_usd(
    chain: str,
    quote_symbol: str,
    amount: float,
) -> float:
    """
    Converts a quote-token amount to USD.

    For the native wrapped asset, the current
    native price is used.

    For stablecoins, the amount is treated
    approximately as USD.
    """

    symbol = (
        str(quote_symbol or "")
        .strip()
        .upper()
    )

    amount = safe_float(
        amount
    )

    if amount <= 0:
        return 0.0

    if symbol in (
        "USDT",
        "USDC",
        "DAI",
        "BUSD",
    ):
        return amount

    native_symbol = get_native_symbol(
        chain
    ).upper()

    if symbol == native_symbol:
        return await native_amount_to_usd(
            chain,
            amount,
        )

    return 0.0
