import httpx


DEX_BASE_URL = (
    "https://api.dexscreener.com"
)


REQUEST_TIMEOUT_SECONDS = 15


CHAIN_MAP = {
    "bnb": "bsc",
    "ethereum": "ethereum",
    "solana": "solana",
}


def normalize_chain(
    chain: str,
):
    if not chain:
        return None

    value = str(
        chain
    ).strip().lower()

    aliases = {
        "bsc": "bnb",
        "binance": "bnb",
        "binance smart chain": "bnb",
        "eth": "ethereum",
        "ethereum": "ethereum",
        "sol": "solana",
        "solana": "solana",
        "bnb": "bnb",
        "robinhood": "robinhood",
        "robinhood chain": "robinhood",
        "rh": "robinhood",
    }

    return aliases.get(
        value
    )


def dex_chain_id(
    chain: str,
):
    normalized = normalize_chain(
        chain
    )

    return CHAIN_MAP.get(
        normalized
    )


def safe_float(
    value,
):
    try:
        return float(
            value or 0
        )

    except (
        ValueError,
        TypeError,
    ):
        return 0.0


def safe_int(
    value,
):
    try:
        return int(
            value or 0
        )

    except (
        ValueError,
        TypeError,
    ):
        return 0


async def get_token_pairs(
    chain: str,
    contract_address: str,
):
    dex_chain = dex_chain_id(
        chain
    )

    if not dex_chain:
        return []

    if not contract_address:
        return []

    contract_address = str(
        contract_address
    ).strip()

    if not contract_address:
        return []

    url = (
        f"{DEX_BASE_URL}/latest/dex/tokens/"
        f"{contract_address}"
    )

    try:

        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS
        ) as client:

            response = await client.get(
                url,
                headers={
                    "Accept": "application/json",
                },
            )

            response.raise_for_status()

            data = response.json()

    except (
        httpx.HTTPError,
        ValueError,
    ) as exc:

        print(
            f"DEX Screener request failed "
            f"for {chain}:{contract_address}: "
            f"{exc}"
        )

        return []

    if not isinstance(
        data,
        dict,
    ):
        return []

    pairs = data.get(
        "pairs"
    ) or []

    if not isinstance(
        pairs,
        list,
    ):
        return []

    # Only return pairs belonging to the
    # selected chain.
    return [
        pair
        for pair in pairs
        if isinstance(
            pair,
            dict,
        )
        and pair.get(
            "chainId"
        ) == dex_chain
    ]


def choose_best_pair(
    pairs,
):
    if not pairs:
        return None

    valid_pairs = [
        pair
        for pair in pairs
        if isinstance(
            pair,
            dict,
        )
    ]

    if not valid_pairs:
        return None

    def pair_score(
        pair,
    ):
        liquidity = safe_float(
            (
                pair.get(
                    "liquidity"
                )
                or {}
            ).get(
                "usd"
            )
        )

        volume = safe_float(
            (
                pair.get(
                    "volume"
                )
                or {}
            ).get(
                "h24"
            )
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

        buys = safe_int(
            h24.get(
                "buys"
            )
        )

        sells = safe_int(
            h24.get(
                "sells"
            )
        )

        activity = (
            buys + sells
        )

        # Liquidity is the primary selection factor.
        # Volume/activity break ties between otherwise
        # similarly liquid pairs.
        return (
            liquidity,
            volume,
            activity,
        )

    return max(
        valid_pairs,
        key=pair_score,
    )


def parse_pair(
    pair,
):
    if not pair:
        return None

    if not isinstance(
        pair,
        dict,
    ):
        return None

    base = (
        pair.get(
            "baseToken"
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

    h24_txns = (
        txns.get(
            "h24"
        )
        or {}
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

        "price_usd": safe_float(
            pair.get(
                "priceUsd"
            )
        ),

        "liquidity_usd": safe_float(
            liquidity.get(
                "usd"
            )
        ),

        "volume_24h": safe_float(
            volume.get(
                "h24"
            )
        ),

        "market_cap": safe_float(
            pair.get(
                "marketCap"
            )
        ),

        "fdv": safe_float(
            pair.get(
                "fdv"
            )
        ),

        "price_change_5m": safe_float(
            price_change.get(
                "m5"
            )
        ),

        "price_change_1h": safe_float(
            price_change.get(
                "h1"
            )
        ),

        "price_change_6h": safe_float(
            price_change.get(
                "h6"
            )
        ),

        "price_change_24h": safe_float(
            price_change.get(
                "h24"
            )
        ),

        "buys_5m": safe_int(
            (
                txns.get(
                    "m5"
                )
                or {}
            ).get(
                "buys"
            )
        ),

        "sells_5m": safe_int(
            (
                txns.get(
                    "m5"
                )
                or {}
            ).get(
                "sells"
            )
        ),

        "buys_1h": safe_int(
            (
                txns.get(
                    "h1"
                )
                or {}
            ).get(
                "buys"
            )
        ),

        "sells_1h": safe_int(
            (
                txns.get(
                    "h1"
                )
                or {}
            ).get(
                "sells"
            )
        ),

        "buys_6h": safe_int(
            (
                txns.get(
                    "h6"
                )
                or {}
            ).get(
                "buys"
            )
        ),

        "sells_6h": safe_int(
            (
                txns.get(
                    "h6"
                )
                or {}
            ).get(
                "sells"
            )
        ),

        "buys_24h": safe_int(
            h24_txns.get(
                "buys"
            )
        ),

        "sells_24h": safe_int(
            h24_txns.get(
                "sells"
            )
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

        "quote_token": (
            pair.get(
                "quoteToken"
            )
            or {}
        ).get(
            "address"
        ),

        "quote_symbol": (
            pair.get(
                "quoteToken"
            )
            or {}
        ).get(
            "symbol"
        ),

        "quote_name": (
            pair.get(
                "quoteToken"
            )
            or {}
        ).get(
            "name"
        ),
    }


async def get_best_token_pair(
    chain: str,
    contract_address: str,
):
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
