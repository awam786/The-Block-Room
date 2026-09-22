import base64
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import httpx

from config import (
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    HELIUS_API_KEY,
    ROBINHOOD_RPC_URL,
)


# =========================================================
# CONSTANTS
# =========================================================

CHAIN_BNB = "BNB"
CHAIN_ETHEREUM = "Ethereum"
CHAIN_SOLANA = "Solana"
CHAIN_ROBINHOOD = "Robinhood"

SUPPORTED_CHAINS = {
    CHAIN_BNB,
    CHAIN_ETHEREUM,
    CHAIN_SOLANA,
    CHAIN_ROBINHOOD,
}


EVM_CHAIN_IDS = {
    CHAIN_ETHEREUM: 1,
    CHAIN_BNB: 56,
    CHAIN_ROBINHOOD: 4663,
}


DEXSCREENER_TOKEN_URL = (
    "https://api.dexscreener.com"
    "/latest/dex/tokens"
)


DEXSCREENER_SEARCH_URL = (
    "https://api.dexscreener.com"
    "/latest/dex/search"
)


SOLANA_RPC_URL = (
    "https://api.mainnet-beta.solana.com"
)


HELIUS_RPC_URL = (
    "https://mainnet.helius-rpc.com/"
)


# =========================================================
# ADDRESS VALIDATION
# =========================================================

EVM_ADDRESS_PATTERN = re.compile(
    r"^0x[a-fA-F0-9]{40}$"
)


BASE58_PATTERN = re.compile(
    r"^[1-9A-HJ-NP-Za-km-z]+$"
)


def normalize_chain(
    chain: str,
) -> Optional[str]:

    if not chain:
        return None

    value = chain.strip().lower()

    mapping = {
        "bnb": CHAIN_BNB,
        "bsc": CHAIN_BNB,
        "bnb smart chain": CHAIN_BNB,

        "ethereum": CHAIN_ETHEREUM,
        "eth": CHAIN_ETHEREUM,

        "solana": CHAIN_SOLANA,
        "sol": CHAIN_SOLANA,

        "robinhood": CHAIN_ROBINHOOD,
        "robinhood chain": CHAIN_ROBINHOOD,
        "rh": CHAIN_ROBINHOOD,
    }

    return mapping.get(value)


def is_evm_address(
    address: str,
) -> bool:

    if not address:
        return False

    return bool(
        EVM_ADDRESS_PATTERN.fullmatch(
            address.strip()
        )
    )


def is_solana_address(
    address: str,
) -> bool:

    if not address:
        return False

    address = address.strip()

    if not (
        32 <= len(address) <= 44
    ):
        return False

    return bool(
        BASE58_PATTERN.fullmatch(
            address
        )
    )


def is_valid_address_format(
    chain: str,
    address: str,
) -> bool:

    normalized = normalize_chain(
        chain
    )

    if normalized in EVM_CHAIN_IDS:
        return is_evm_address(address)

    if normalized == CHAIN_SOLANA:
        return is_solana_address(address)

    return False


# =========================================================
# RPC HELPERS
# =========================================================

async def rpc_call(
    rpc_url: str,
    method: str,
    params: list,
    timeout: float = 15.0,
) -> Dict[str, Any]:

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    async with httpx.AsyncClient(
        timeout=timeout
    ) as client:

        response = await client.post(
            rpc_url,
            json=payload,
        )

        response.raise_for_status()

        data = response.json()

    if "error" in data:
        raise RuntimeError(
            str(data["error"])
        )

    return data


def get_evm_rpc(
    chain: str,
) -> Optional[str]:

    normalized = normalize_chain(
        chain
    )

    if normalized == CHAIN_BNB:
        return BNB_RPC_URL

    if normalized == CHAIN_ETHEREUM:
        return ETHEREUM_RPC_URL

    if normalized == CHAIN_ROBINHOOD:
        return ROBINHOOD_RPC_URL

    return None


# =========================================================
# EVM ADDRESS CHECK
# =========================================================

async def verify_evm_contract(
    chain: str,
    address: str,
) -> Dict[str, Any]:

    normalized = normalize_chain(
        chain
    )

    if normalized not in EVM_CHAIN_IDS:
        return {
            "valid": False,
            "reason": "Unsupported EVM chain.",
        }

    if not is_evm_address(address):
        return {
            "valid": False,
            "reason": "Invalid EVM address format.",
        }

    rpc_url = get_evm_rpc(
        normalized
    )

    if not rpc_url:
        return {
            "valid": False,
            "reason": "RPC is not configured.",
        }

    try:
        code_result = await rpc_call(
            rpc_url,
            "eth_getCode",
            [
                address,
                "latest",
            ],
        )

        code = code_result.get(
            "result"
        )

        if not code or code == "0x":
            return {
                "valid": False,
                "reason": (
                    "No smart contract exists "
                    "at this address on the "
                    "selected chain."
                ),
            }

        chain_result = await rpc_call(
            rpc_url,
            "eth_chainId",
            [],
        )

        chain_hex = chain_result.get(
            "result"
        )

        actual_chain_id = int(
            chain_hex,
            16,
        )

        expected_chain_id = (
            EVM_CHAIN_IDS[
                normalized
            ]
        )

        if actual_chain_id != expected_chain_id:
            return {
                "valid": False,
                "reason": (
                    "RPC returned a different "
                    "chain ID."
                ),
            }

        return {
            "valid": True,
            "chain": normalized,
            "address": address,
            "chain_id": actual_chain_id,
            "contract_code": code,
        }

    except Exception as exc:
        return {
            "valid": False,
            "reason": (
                "Unable to verify contract "
                f"on {normalized}: {exc}"
            ),
        }


# =========================================================
# SOLANA ADDRESS CHECK
# =========================================================

async def verify_solana_address(
    address: str,
) -> Dict[str, Any]:

    if not is_solana_address(address):
        return {
            "valid": False,
            "reason": "Invalid Solana address format.",
        }

    try:
        result = await rpc_call(
            SOLANA_RPC_URL,
            "getAccountInfo",
            [
                address,
                {
                    "encoding": "base64"
                },
            ],
        )

        value = (
            result
            .get("result", {})
            .get("value")
        )

        if value is None:
            return {
                "valid": False,
                "reason": (
                    "Address does not exist "
                    "on Solana."
                ),
            }

        return {
            "valid": True,
            "chain": CHAIN_SOLANA,
            "address": address,
            "account": value,
        }

    except Exception as exc:
        return {
            "valid": False,
            "reason": (
                "Unable to verify Solana "
                f"address: {exc}"
            ),
        }


# =========================================================
# UNIFIED ADDRESS VALIDATION
# =========================================================

async def validate_token_address(
    chain: str,
    address: str,
) -> Dict[str, Any]:

    normalized = normalize_chain(
        chain
    )

    if not normalized:
        return {
            "valid": False,
            "reason": "Unsupported chain.",
        }

    if normalized == CHAIN_SOLANA:
        return await verify_solana_address(
            address
        )

    return await verify_evm_contract(
        normalized,
        address,
    )


# =========================================================
# ERC-20 CALL HELPERS
# =========================================================

def encode_string_function(
    selector: str,
) -> str:

    return selector


def decode_abi_string(
    data: str,
) -> Optional[str]:

    if not data or data == "0x":
        return None

    raw = data[2:]

    try:
        # Dynamic ABI string
        if len(raw) >= 128:
            offset = int(
                raw[0:64],
                16,
            )

            length_position = (
                offset * 2
            )

            if (
                length_position + 64
                <= len(raw)
            ):
                length = int(
                    raw[
                        length_position:
                        length_position + 64
                    ],
                    16,
                )

                start = (
                    length_position + 64
                )

                end = (
                    start
                    + length * 2
                )

                value = bytes.fromhex(
                    raw[start:end]
                ).decode(
                    "utf-8",
                    errors="ignore",
                )

                return value.strip(
                    "\x00"
                )

        # bytes32 fallback
        if len(raw) >= 64:
            value = bytes.fromhex(
                raw[:64]
            ).decode(
                "utf-8",
                errors="ignore",
            )

            return value.strip(
                "\x00"
            )

    except Exception:
        return None

    return None


async def evm_eth_call(
    chain: str,
    address: str,
    data: str,
) -> Optional[str]:

    rpc_url = get_evm_rpc(
        chain
    )

    if not rpc_url:
        return None

    try:
        result = await rpc_call(
            rpc_url,
            "eth_call",
            [
                {
                    "to": address,
                    "data": data,
                },
                "latest",
            ],
        )

        return result.get(
            "result"
        )

    except Exception:
        return None


async def get_evm_token_metadata(
    chain: str,
    address: str,
) -> Dict[str, Any]:

    metadata = {
        "name": None,
        "symbol": None,
        "decimals": None,
    }

    # name()
    name_result = await evm_eth_call(
        chain,
        address,
        "0x06fdde03",
    )

    if name_result:
        metadata["name"] = (
            decode_abi_string(
                name_result
            )
        )

    # symbol()
    symbol_result = await evm_eth_call(
        chain,
        address,
        "0x95d89b41",
    )

    if symbol_result:
        metadata["symbol"] = (
            decode_abi_string(
                symbol_result
            )
        )

    # decimals()
    decimals_result = await evm_eth_call(
        chain,
        address,
        "0x313ce567",
    )

    if decimals_result:
        try:
            metadata["decimals"] = int(
                decimals_result,
                16,
            )
        except Exception:
            pass

    return metadata


# =========================================================
# SOLANA HELIUS METADATA
# =========================================================

async def get_solana_token_metadata(
    address: str,
) -> Dict[str, Any]:

    if not HELIUS_API_KEY:
        return {
            "name": None,
            "symbol": None,
            "decimals": None,
        }

    url = (
        f"{HELIUS_RPC_URL}"
        f"?api-key={HELIUS_API_KEY}"
    )

    payload = {
        "jsonrpc": "2.0",
        "id": "block-room",
        "method": "getAsset",
        "params": {
            "id": address,
        },
    }

    try:
        async with httpx.AsyncClient(
            timeout=15
        ) as client:

            response = await client.post(
                url,
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

        result = data.get(
            "result"
        ) or {}

        content = (
            result.get("content")
            or {}
        )

        metadata = (
            content.get("metadata")
            or {}
        )

        token_info = (
            result.get("token_info")
            or {}
        )

        decimals = token_info.get(
            "decimals"
        )

        return {
            "name": metadata.get(
                "name"
            ),
            "symbol": metadata.get(
                "symbol"
            ),
            "decimals": decimals,
        }

    except Exception as exc:
        print(
            "Helius metadata error: "
            f"{exc}"
        )

        return {
            "name": None,
            "symbol": None,
            "decimals": None,
        }


# =========================================================
# DEX SCREENER DATA
# =========================================================

def choose_best_pair(
    pairs: list,
) -> Optional[Dict[str, Any]]:

    if not pairs:
        return None

    def score(pair):

        liquidity = (
            pair.get(
                "liquidity",
                {}
            ).get(
                "usd"
            )
            or 0
        )

        volume = (
            pair.get(
                "volume",
                {}
            ).get(
                "h24"
            )
            or 0
        )

        return (
            float(liquidity or 0),
            float(volume or 0),
        )

    return max(
        pairs,
        key=score,
    )


async def get_dex_pairs(
    address: str,
) -> list:

    url = (
        f"{DEXSCREENER_TOKEN_URL}"
        f"/{address}"
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

        return data.get(
            "pairs"
        ) or []

    except Exception as exc:
        print(
            "DEX Screener token lookup "
            f"failed: {exc}"
        )

        return []


async def search_dex(
    query: str,
) -> list:

    try:
        async with httpx.AsyncClient(
            timeout=15
        ) as client:

            response = await client.get(
                DEXSCREENER_SEARCH_URL,
                params={
                    "q": query,
                },
            )

            response.raise_for_status()

            data = response.json()

        return data.get(
            "pairs"
        ) or []

    except Exception as exc:
        print(
            "DEX Screener search failed: "
            f"{exc}"
        )

        return []


# =========================================================
# CHAIN MATCHING
# =========================================================

def dex_chain_matches(
    chain: str,
    pair: Dict[str, Any],
) -> bool:

    normalized = normalize_chain(
        chain
    )

    dex_chain_id = (
        pair.get("chainId")
        or ""
    ).lower()

    if normalized == CHAIN_BNB:
        return dex_chain_id in (
            "bsc",
            "bnb",
        )

    if normalized == CHAIN_ETHEREUM:
        return dex_chain_id in (
            "ethereum",
            "eth",
        )

    if normalized == CHAIN_SOLANA:
        return dex_chain_id == "solana"

    if normalized == CHAIN_ROBINHOOD:
        return dex_chain_id in (
            "robinhood",
            "robinhood-chain",
        )

    return False


# =========================================================
# TOKEN MARKET DATA
# =========================================================

async def get_token_market_data(
    chain: str,
    address: str,
) -> Dict[str, Any]:

    normalized = normalize_chain(
        chain
    )

    if not normalized:
        return {
            "found": False,
            "reason": "Unsupported chain.",
        }

    pairs = await get_dex_pairs(
        address
    )

    matching_pairs = [
        pair
        for pair in pairs
        if dex_chain_matches(
            normalized,
            pair,
        )
    ]

    pair = choose_best_pair(
        matching_pairs
    )

    if not pair:

        # Some DEX indexing situations may
        # not expose a direct chain match yet.
        # Return no pair rather than guessing.

        return {
            "found": False,
            "chain": normalized,
            "address": address,
            "reason": (
                "No matching DEX pair found."
            ),
            "pairs": [],
        }

    liquidity = (
        pair.get(
            "liquidity",
            {}
        ).get("usd")
        or 0
    )

    volume_24h = (
        pair.get(
            "volume",
            {}
        ).get("h24")
        or 0
    )

    market_cap = (
        pair.get("marketCap")
        or pair.get("fdv")
        or 0
    )

    price_change = (
        pair.get(
            "priceChange",
            {}
        )
    )

    price_momentum = (
        price_change.get("h1")
        or 0
    )

    recent_txns = (
        pair.get(
            "txns",
            {}
        ).get("h1")
        or {}
    )

    buys = (
        recent_txns.get("buys")
        or 0
    )

    sells = (
        recent_txns.get("sells")
        or 0
    )

    recent_activity = (
        buys + sells
    )

    return {
        "found": True,
        "chain": normalized,
        "address": address,
        "pair_address": pair.get(
            "pairAddress"
        ),
        "dex_id": pair.get(
            "dexId"
        ),
        "dex_url": pair.get(
            "url"
        ),
        "base_token": (
            pair.get(
                "baseToken"
            )
            or {}
        ),
        "quote_token": (
            pair.get(
                "quoteToken"
            )
            or {}
        ),
        "price_usd": pair.get(
            "priceUsd"
        ),
        "liquidity_usd": float(
            liquidity or 0
        ),
        "volume_24h": float(
            volume_24h or 0
        ),
        "market_cap_usd": float(
            market_cap or 0
        ),
        "price_momentum": float(
            price_momentum or 0
        ),
        "buys_1h": int(
            buys or 0
        ),
        "sells_1h": int(
            sells or 0
        ),
        "recent_activity": int(
            recent_activity or 0
        ),
        "pair_created_at": pair.get(
            "pairCreatedAt"
        ),
        "raw_pair": pair,
    }


# =========================================================
# COMPLETE TOKEN LOOKUP
# =========================================================

async def get_token_info(
    chain: str,
    address: str,
) -> Dict[str, Any]:

    normalized = normalize_chain(
        chain
    )

    validation = await validate_token_address(
        normalized,
        address,
    )

    if not validation.get(
        "valid"
    ):
        return validation

    metadata = {
        "name": None,
        "symbol": None,
        "decimals": None,
    }

    if normalized in EVM_CHAIN_IDS:

        metadata = (
            await get_evm_token_metadata(
                normalized,
                address,
            )
        )

    elif normalized == CHAIN_SOLANA:

        metadata = (
            await get_solana_token_metadata(
                address
            )
        )

    market = await get_token_market_data(
        normalized,
        address,
    )

    result = {
        "valid": True,
        "chain": normalized,
        "address": address,
        "name": metadata.get(
            "name"
        ),
        "symbol": metadata.get(
            "symbol"
        ),
        "decimals": metadata.get(
            "decimals"
        ),
        "market": market,
    }

    return result


# =========================================================
# TOKEN STATUS
# =========================================================

async def get_token_status(
    chain: str,
    address: str,
) -> Dict[str, Any]:

    info = await get_token_info(
        chain,
        address,
    )

    if not info.get("valid"):
        return {
            "status": "INVALID",
            "valid": False,
            "reason": info.get(
                "reason",
                "Token validation failed.",
            ),
        }

    market = info.get(
        "market"
    ) or {}

    if not market.get("found"):
        return {
            **info,
            "status": "NOT_LAUNCHED",
            "reason": (
                "Contract exists, but no "
                "matching DEX pair was found."
            ),
        }

    liquidity = (
        market.get(
            "liquidity_usd"
        )
        or 0
    )

    volume = (
        market.get(
            "volume_24h"
        )
        or 0
    )

    if liquidity <= 0:
        return {
            **info,
            "status": "NOT_LAUNCHED",
            "reason": (
                "No usable liquidity found."
            ),
        }

    if volume <= 0:
        return {
            **info,
            "status": "LOW_ACTIVITY",
            "reason": (
                "Pair exists but currently "
                "has no 24h volume."
            ),
        }

    return {
        **info,
        "status": "LIVE",
        "reason": "Active market pair found.",
    }
