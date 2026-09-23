import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx


DEX_BASE_URL = "https://api.dexscreener.com"


CHAIN_IDS = {
    "bnb": "bsc",
    "ethereum": "ethereum",
    "solana": "solana",
    "robinhood": "robinhood",
}


WRAPPED_NATIVE = {
    "bnb": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "solana": "So11111111111111111111111111111111111111112",
    "robinhood": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
}


NATIVE_SYMBOLS = {
    "bnb": "BNB",
    "ethereum": "ETH",
    "solana": "SOL",
    "robinhood": "ETH",
}


STABLECOINS = {
    "bnb": {
        "0x55d398326f99059ff775485246999027b3197955",
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    },
    "ethereum": {
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0x6b175474e89094c44da98b954eedeac495271d0f",
    },
    "robinhood": {
        "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
    },
    "solana": {
        "epjfwdd5auoahk5csp8q7j2x5p6m5h4j4f6x7h8j9k",
        "es9vmfrzacer m jfrf4h2fyd4kconk11mcce8benwnyb"
            .replace(" ", ""),
    },
}


CACHE_SECONDS = 20


_market_cache: Dict[str, Any] = {}
_cache_lock = asyncio.Lock()


def normalize_chain(
    chain: str,
) -> str:
    value = (
        str(chain)
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
    address: str,
) -> str:
    return str(address).strip()


def _cache_key(
    chain: str,
    address: str,
) -> str:
    return (
        f"{normalize_chain(chain)}:"
        f"{normalize_address(address).lower()}"
    )


async def _http_get_json(
    url: str,
) -> Optional[Any]:
    try:
        async with httpx.AsyncClient(
            timeout=15
        ) as client:
            response = await client.get(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": (
                        "TheBlockRoom/1.0"
                    ),
                },
            )

            response.raise_for_status()

            return response.json()

    except Exception as exc:
        print(
            "Market-data HTTP error: "
            f"{exc}"
        )
        return None


async def get_token_pairs(
    chain: str,
    token_address: str,
) -> List[Dict[str, Any]]:
    chain = normalize_chain(chain)

    chain_id = CHAIN_IDS.get(chain)

    if not chain_id:
        return []

    address = normalize_address(
        token_address
    )

    if not address:
        return []

    url = (
        f"{DEX_BASE_URL}/token-pairs/v1/"
        f"{chain_id}/{address}"
    )

    data = await _http_get_json(url)

    if not isinstance(data, list):
        return []

    return data


def _safe_float(
    value: Any,
) -> float:
    try:
        if value is None:
            return 0.0

        return float(value)

    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def _safe_int(
    value: Any,
) -> int:
    try:
        if value is None:
            return 0

        return int(value)

    except (
        TypeError,
        ValueError,
    ):
        return 0


def select_best_pair(
    pairs: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not pairs:
        return None

    def score(
        pair: Dict[str, Any],
    ):
        liquidity = _safe_float(
            pair.get("liquidity", {}).get(
                "usd"
            )
        )

        volume_24h = _safe_float(
            pair.get("volume", {}).get(
                "h24"
            )
        )

        txns = pair.get(
            "txns",
            {},
        )

        buys = _safe_int(
            txns.get("h24", {}).get(
                "buys"
            )
        )

        sells = _safe_int(
            txns.get("h24", {}).get(
                "sells"
            )
        )

        activity = buys + sells

        return (
            liquidity,
            volume_24h,
            activity,
        )

    return max(
        pairs,
        key=score,
    )


# Compatibility alias used by other services.
choose_best_pair = select_best_pair


def parse_pair(
    pair: Dict[str, Any],
) -> Dict[str, Any]:
    base = pair.get(
        "baseToken",
        {}
    )

    quote = pair.get(
        "quoteToken",
        {}
    )

    liquidity = pair.get(
        "liquidity",
        {}
    )

    volume = pair.get(
        "volume",
        {}
    )

    price_change = pair.get(
        "priceChange",
        {}
    )

    txns = pair.get(
        "txns",
        {}
    )

    tx_5m = txns.get(
        "m5",
        {}
    )

    tx_1h = txns.get(
        "h1",
        {}
    )

    tx_6h = txns.get(
        "h6",
        {}
    )

    tx_24h = txns.get(
        "h24",
        {}
    )

    return {
        "name": base.get(
            "name"
        ),
        "symbol": base.get(
            "symbol"
        ),
        "address": base.get(
            "address"
        ),

        "pair_address": pair.get(
            "pairAddress"
        ),

        "dex": pair.get(
            "dexId"
        ),

        "chain": pair.get(
            "chainId"
        ),

        "url": pair.get(
            "url"
        ),

        "dex_url": pair.get(
            "url"
        ),

        "price_usd": _safe_float(
            pair.get("priceUsd")
        ),

        "price_native": _safe_float(
            pair.get("priceNative")
        ),

        "liquidity_usd": _safe_float(
            liquidity.get("usd")
        ),

        "liquidity_base": _safe_float(
            liquidity.get("base")
        ),

        "liquidity_quote": _safe_float(
            liquidity.get("quote")
        ),

        "market_cap": _safe_float(
            pair.get("marketCap")
        ),

        "fdv": _safe_float(
            pair.get("fdv")
        ),

        "volume_5m": _safe_float(
            volume.get("m5")
        ),

        "volume_1h": _safe_float(
            volume.get("h1")
        ),

        "volume_6h": _safe_float(
            volume.get("h6")
        ),

        "volume_24h": _safe_float(
            volume.get("h24")
        ),

        "price_change_5m": _safe_float(
            price_change.get("m5")
        ),

        "price_change_1h": _safe_float(
            price_change.get("h1")
        ),

        "price_change_6h": _safe_float(
            price_change.get("h6")
        ),

        "price_change_24h": _safe_float(
            price_change.get("h24")
        ),

        "buys_5m": _safe_int(
            tx_5m.get("buys")
        ),

        "sells_5m": _safe_int(
            tx_5m.get("sells")
        ),

        "buys_1h": _safe_int(
            tx_1h.get("buys")
        ),

        "sells_1h": _safe_int(
            tx_1h.get("sells")
        ),

        "buys_6h": _safe_int(
            tx_6h.get("buys")
        ),

        "sells_6h": _safe_int(
            tx_6h.get("sells")
        ),

        "buys_24h": _safe_int(
            tx_24h.get("buys")
        ),

        "sells_24h": _safe_int(
            tx_24h.get("sells")
        ),

        "pair_created_at": pair.get(
            "pairCreatedAt"
        ),

        "quote_token": {
            "name": quote.get(
                "name"
            ),
            "symbol": quote.get(
                "symbol"
            ),
            "address": quote.get(
                "address"
            ),
        },

        "labels": pair.get(
            "labels",
            [],
        ),

        "image_url": pair.get(
            "info",
            {}
        ).get(
            "imageUrl"
        ),
    }


async def get_token_market_data(
    chain: str,
    token_address: str,
) -> Optional[Dict[str, Any]]:
    chain = normalize_chain(chain)

    key = _cache_key(
        chain,
        token_address,
    )

    now = time.monotonic()

    async with _cache_lock:
        cached = _market_cache.get(
            key
        )

        if cached:
            timestamp, value = cached

            if (
                now - timestamp
                < CACHE_SECONDS
            ):
                return value

    pairs = await get_token_pairs(
        chain,
        token_address,
    )

    best = select_best_pair(
        pairs
    )

    if not best:
        return None

    parsed = parse_pair(
        best
    )

    async with _cache_lock:
        _market_cache[key] = (
            now,
            parsed,
        )

    return parsed


async def get_market_data_safe(
    chain: str,
    token_address: str,
) -> Dict[str, Any]:
    try:
        data = await get_token_market_data(
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

    return {}


async def get_native_price_usd(
    chain: str,
) -> float:
    """
    Gets the native asset USD price.

    BNB:
        WBNB

    Ethereum:
        WETH

    Solana:
        WSOL

    Robinhood:
        WETH, because Robinhood Chain uses ETH
        as its native gas asset.
    """

    chain = normalize_chain(chain)

    wrapped = WRAPPED_NATIVE.get(
        chain
    )

    if not wrapped:
        return 0.0

    pairs = await get_token_pairs(
        chain,
        wrapped,
    )

    best = select_best_pair(
        pairs
    )

    if not best:
        return 0.0

    parsed = parse_pair(
        best
    )

    return _safe_float(
        parsed.get(
            "price_usd"
        )
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


async def quote_amount_to_usd(
    chain: str,
    quote_address: str,
    amount: float,
) -> float:
    """
    Converts a quote-token amount into USD.

    Stablecoins are treated as approximately $1.

    Wrapped native assets use the current native
    market price.

    Other quote tokens are priced through DEX
    market data when available.
    """

    chain = normalize_chain(chain)

    quote_address = normalize_address(
        quote_address
    )

    if amount <= 0:
        return 0.0

    if (
        quote_address.lower()
        in STABLECOINS.get(
            chain,
            set(),
        )
    ):
        return amount

    wrapped = WRAPPED_NATIVE.get(
        chain
    )

    if (
        wrapped
        and quote_address.lower()
        == wrapped.lower()
    ):
        price = await get_native_price_usd(
            chain
        )

        return amount * price

    quote_data = (
        await get_token_market_data(
            chain,
            quote_address,
        )
    )

    if not quote_data:
        return 0.0

    price = _safe_float(
        quote_data.get(
            "price_usd"
        )
    )

    return amount * price


async def native_amount_to_usd(
    chain: str,
    amount: float,
) -> float:
    if amount <= 0:
        return 0.0

    price = await get_native_price_usd(
        chain
    )

    return amount * price


async def clear_market_cache():
    async with _cache_lock:
        _market_cache.clear()


async def cleanup_market_cache():
    now = time.monotonic()

    async with _cache_lock:
        expired = [
            key
            for key, value
            in _market_cache.items()
            if (
                now - value[0]
                >= CACHE_SECONDS
            )
        ]

        for key in expired:
            _market_cache.pop(
                key,
                None,
            )
