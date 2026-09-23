from typing import Any

import httpx


# ============================================================
# CONFIGURATION
# ============================================================

DEX_BASE_URL = (
    "https://api.dexscreener.com"
)

REQUEST_TIMEOUT = httpx.Timeout(
    15.0,
    connect=10.0,
)


# DEX Screener chain IDs.
#
# Robinhood Chain is represented by "robinhood"
# on DEX Screener.
CHAIN_MAP = {
    "bnb": "bsc",
    "bsc": "bsc",
    "binance": "bsc",
    "binance smart chain": "bsc",

    "ethereum": "ethereum",
    "eth": "ethereum",

    "solana": "solana",
    "sol": "solana",

    "robinhood": "robinhood",
    "rh": "robinhood",
    "robinhood chain": "robinhood",
}


# ============================================================
# HELPERS
# ============================================================

def normalize_chain(
    chain: str,
) -> str:
    value = (
        chain or ""
    ).strip().lower()

    return CHAIN_MAP.get(
        value,
        value,
    )


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


def normalize_address(
    address: str,
) -> str:
    return (
        address or ""
    ).strip().lower()


# ============================================================
# GET TOKEN PAIRS
# ============================================================

async def get_token_pairs(
    chain: str,
    contract_address: str,
) -> list[dict]:
    """
    Return all DEX Screener pairs for a token.

    Supported:
        BNB Smart Chain
        Ethereum
        Solana
        Robinhood Chain
    """

    chain_id = normalize_chain(
        chain
    )

    contract_address = (
        contract_address or ""
    ).strip()

    if not chain_id or not contract_address:
        return []

    url = (
        f"{DEX_BASE_URL}"
        f"/token-pairs/v1/"
        f"{chain_id}/"
        f"{contract_address}"
    )

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT
        ) as client:

            response = await client.get(
                url
            )

            response.raise_for_status()

            data = response.json()

    except Exception as exc:
        print(
            "DEX Screener token lookup error "
            f"chain={chain_id} "
            f"token={contract_address}: "
            f"{exc}"
        )

        return []

    if not isinstance(
        data,
        list,
    ):
        return []

    pairs = []

    for pair in data:
        if not isinstance(
            pair,
            dict,
        ):
            continue

        returned_chain = (
            pair.get("chainId")
            or ""
        )

        if (
            returned_chain
            and returned_chain.lower()
            != chain_id.lower()
        ):
            continue

        pairs.append(
            pair
        )

    return pairs


# ============================================================
# CHOOSE BEST PAIR
# ============================================================

def choose_best_pair(
    pairs: list[dict],
) -> dict | None:
    """
    Select the most useful trading pair.

    Priority:
        1. Liquidity
        2. 24h volume
        3. Recent transaction activity
    """

    if not pairs:
        return None

    def pair_score(
        pair: dict,
    ):
        liquidity = safe_float(
            (
                pair.get("liquidity")
                or {}
            ).get("usd")
        )

        volume_24h = safe_float(
            (
                pair.get("volume")
                or {}
            ).get("h24")
        )

        txns = (
            pair.get("txns")
            or {}
        )

        h24 = (
            txns.get("h24")
            or {}
        )

        buys = safe_int(
            h24.get("buys")
        )

        sells = safe_int(
            h24.get("sells")
        )

        transactions = (
            buys + sells
        )

        return (
            liquidity,
            volume_24h,
            transactions,
        )

    return max(
        pairs,
        key=pair_score,
    )


# Backward-compatible alias.
choose_best_pair = choose_best_pair


# ============================================================
# PARSE PAIR
# ============================================================

def parse_pair(
    pair: dict,
) -> dict:
    """
    Convert raw DEX Screener pair data into
    the normalized structure used by the bot.
    """

    base_token = (
        pair.get("baseToken")
        or {}
    )

    quote_token = (
        pair.get("quoteToken")
        or {}
    )

    txns = (
        pair.get("txns")
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

    liquidity = (
        pair.get("liquidity")
        or {}
    )

    h5 = (
        txns.get("m5")
        or {}
    )

    h1 = (
        txns.get("h1")
        or {}
    )

    h6 = (
        txns.get("h6")
        or {}
    )

    h24 = (
        txns.get("h24")
        or {}
    )

    base_address = (
        base_token.get("address")
        or ""
    )

    return {
        # ----------------------------------------------------
        # Basic token information
        # ----------------------------------------------------

        "name": (
            base_token.get("name")
            or "Unknown"
        ),

        "symbol": (
            base_token.get("symbol")
            or "UNKNOWN"
        ),

        "address": base_address,

        # ----------------------------------------------------
        # Pair information
        # ----------------------------------------------------

        "pair_address": (
            pair.get("pairAddress")
            or ""
        ),

        "dex": (
            pair.get("dexId")
            or "Unknown"
        ),

        "chain": (
            pair.get("chainId")
            or ""
        ),

        "url": (
            pair.get("url")
            or ""
        ),

        "dex_url": (
            pair.get("url")
            or ""
        ),

        # ----------------------------------------------------
        # Price
        # ----------------------------------------------------

        "price_usd": safe_float(
            pair.get("priceUsd")
        ),

        "price_native": safe_float(
            pair.get("priceNative")
        ),

        # ----------------------------------------------------
        # Liquidity / valuation
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Volume
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Price changes
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Buy / sell activity
        # ----------------------------------------------------

        "buys_5m": safe_int(
            h5.get("buys")
        ),

        "sells_5m": safe_int(
            h5.get("sells")
        ),

        "buys_1h": safe_int(
            h1.get("buys")
        ),

        "sells_1h": safe_int(
            h1.get("sells")
        ),

        "buys_6h": safe_int(
            h6.get("buys")
        ),

        "sells_6h": safe_int(
            h6.get("sells")
        ),

        "buys_24h": safe_int(
            h24.get("buys")
        ),

        "sells_24h": safe_int(
            h24.get("sells")
        ),

        # ----------------------------------------------------
        # Pair age
        # ----------------------------------------------------

        "pair_created_at": pair.get(
            "pairCreatedAt"
        ),

        # ----------------------------------------------------
        # Quote token
        # ----------------------------------------------------

        "quote_token_address": (
            quote_token.get("address")
            or ""
        ),

        "quote_token_name": (
            quote_token.get("name")
            or ""
        ),

        "quote_token_symbol": (
            quote_token.get("symbol")
            or ""
        ),

        # ----------------------------------------------------
        # DEX metadata
        # ----------------------------------------------------

        "labels": (
            pair.get("labels")
            or []
        ),

        "image_url": (
            (
                pair.get("info")
                or {}
            ).get("imageUrl")
            or ""
        ),
    }


# ============================================================
# GET BEST TOKEN PAIR
# ============================================================

async def get_best_token_pair(
    chain: str,
    contract_address: str,
) -> dict | None:
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


# ============================================================
# COMPATIBILITY HELPERS
# ============================================================

async def get_token_market_data(
    chain: str,
    contract_address: str,
) -> dict | None:
    """
    Compatibility helper used by older services.
    """

    return await get_best_token_pair(
        chain,
        contract_address,
    )
