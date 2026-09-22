import httpx


DEX_BASE_URL = "https://api.dexscreener.com"


CHAIN_MAP = {
    "bnb": "bsc",
    "ethereum": "ethereum",
    "solana": "solana",
}


async def get_token_pairs(
    chain: str,
    contract_address: str,
):
    dex_chain = CHAIN_MAP.get(chain)

    if not dex_chain:
        return []

    url = (
        f"{DEX_BASE_URL}/latest/dex/tokens/"
        f"{contract_address}"
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()

    except (httpx.HTTPError, ValueError):
        return []

    pairs = data.get("pairs") or []

    # Only return pairs belonging to the selected chain.
    return [
        pair
        for pair in pairs
        if pair.get("chainId") == dex_chain
    ]


def choose_best_pair(pairs):
    if not pairs:
        return None

    return max(
        pairs,
        key=lambda pair: (
            pair.get("liquidity", {}).get("usd") or 0
        ),
    )


def parse_pair(pair):
    if not pair:
        return None

    base = pair.get("baseToken") or {}
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    price_change = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}
    h24_txns = txns.get("h24") or {}

    return {
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "address": base.get("address"),
        "pair_address": pair.get("pairAddress"),
        "dex": pair.get("dexId"),
        "price_usd": pair.get("priceUsd"),
        "liquidity_usd": liquidity.get("usd") or 0,
        "volume_24h": volume.get("h24") or 0,
        "market_cap": pair.get("marketCap") or 0,
        "fdv": pair.get("fdv") or 0,
        "price_change_24h": price_change.get("h24") or 0,
        "buys_24h": h24_txns.get("buys") or 0,
        "sells_24h": h24_txns.get("sells") or 0,
        "pair_created_at": pair.get("pairCreatedAt"),
        "url": pair.get("url"),
    }
