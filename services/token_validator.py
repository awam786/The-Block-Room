from typing import Any, Dict, Optional

import httpx

from config import (
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    HELIUS_API_KEY,
    ROBINHOOD_RPC_URL,
)

from services.dex import (
    get_best_token_pair,
)


SUPPORTED_CHAINS = {
    "bnb",
    "ethereum",
    "solana",
    "robinhood",
}


EVM_CHAINS = {
    "bnb",
    "ethereum",
    "robinhood",
}


EVM_RPC_URLS = {
    "bnb": BNB_RPC_URL,
    "ethereum": ETHEREUM_RPC_URL,
    "robinhood": ROBINHOOD_RPC_URL,
}


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
        "binance smart chain": "bnb",
        "bnb smart chain": "bnb",
        "eth": "ethereum",
        "mainnet": "ethereum",
        "sol": "solana",
        "rh": "robinhood",
        "robinhood chain": "robinhood",
    }

    return aliases.get(
        value,
        value,
    )


def normalize_evm_address(
    address: str,
) -> str:
    return (
        str(address or "")
        .strip()
        .lower()
    )


# ============================================================
# ADDRESS VALIDATION
# ============================================================

def is_valid_evm_address(
    address: str,
) -> bool:
    value = str(
        address or ""
    ).strip()

    if len(value) != 42:
        return False

    if not value.startswith(
        "0x"
    ):
        return False

    try:
        int(
            value[2:],
            16,
        )
    except ValueError:
        return False

    return True


def is_valid_solana_address(
    address: str,
) -> bool:
    value = str(
        address or ""
    ).strip()

    if not (
        32
        <= len(value)
        <= 44
    ):
        return False

    alphabet = (
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        "abcdefghijkmnopqrstuvwxyz"
    )

    return all(
        character in alphabet
        for character in value
    )


def is_valid_address(
    chain: str,
    address: str,
) -> bool:
    chain = normalize_chain(
        chain
    )

    if chain in EVM_CHAINS:
        return is_valid_evm_address(
            address
        )

    if chain == "solana":
        return is_valid_solana_address(
            address
        )

    return False


# ============================================================
# EVM RPC
# ============================================================

async def rpc_call(
    rpc_url: str,
    method: str,
    params: list,
) -> Optional[Any]:
    if not rpc_url:
        return None

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    try:
        async with httpx.AsyncClient(
            timeout=20
        ) as client:
            response = await client.post(
                rpc_url,
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

    except Exception as exc:
        print(
            f"RPC error "
            f"method={method}: {exc}"
        )
        return None

    if data.get("error"):
        return None

    return data.get(
        "result"
    )


async def contract_exists(
    chain: str,
    address: str,
) -> bool:
    rpc_url = EVM_RPC_URLS.get(
        chain
    )

    if not rpc_url:
        return False

    code = await rpc_call(
        rpc_url,
        "eth_getCode",
        [
            address,
            "latest",
        ],
    )

    if not isinstance(
        code,
        str,
    ):
        return False

    return code not in {
        "",
        "0x",
        "0x0",
    }


# ============================================================
# ERC-20 TOKEN METADATA
# ============================================================

def encode_address_parameter(
    address: str,
) -> str:
    return (
        "000000000000000000000000"
        + address[2:].lower()
    )


def decode_abi_string(
    value: Optional[str],
) -> Optional[str]:
    if not value:
        return None

    if not value.startswith(
        "0x"
    ):
        return None

    raw = value[2:]

    try:
        # Dynamic ABI string:
        # offset + length + data
        if len(raw) >= 128:
            offset = int(
                raw[0:64],
                16,
            )

            offset_hex = (
                offset * 2
            )

            if (
                offset_hex + 64
                <= len(raw)
            ):
                length = int(
                    raw[
                        offset_hex:
                        offset_hex + 64
                    ],
                    16,
                )

                start = (
                    offset_hex
                    + 64
                )

                end = (
                    start
                    + length * 2
                )

                if end <= len(raw):
                    decoded = bytes.fromhex(
                        raw[start:end]
                    ).decode(
                        "utf-8",
                        errors="ignore",
                    ).strip()

                    if decoded:
                        return decoded

        # bytes32 fallback
        data = bytes.fromhex(
            raw[:64]
        )

        decoded = data.rstrip(
            b"\x00"
        ).decode(
            "utf-8",
            errors="ignore",
        ).strip()

        return decoded or None

    except Exception:
        return None


async def get_erc20_metadata(
    chain: str,
    address: str,
) -> Dict[str, Optional[str]]:
    rpc_url = EVM_RPC_URLS.get(
        chain
    )

    if not rpc_url:
        return {
            "name": None,
            "symbol": None,
        }

    name_result = await rpc_call(
        rpc_url,
        "eth_call",
        [
            {
                "to": address,
                "data": "0x06fdde03",
            },
            "latest",
        ],
    )

    symbol_result = await rpc_call(
        rpc_url,
        "eth_call",
        [
            {
                "to": address,
                "data": "0x95d89b41",
            },
            "latest",
        ],
    )

    return {
        "name": decode_abi_string(
            name_result
        ),
        "symbol": decode_abi_string(
            symbol_result
        ),
    }


# ============================================================
# SOLANA METADATA
# ============================================================

async def get_solana_asset(
    address: str,
) -> Optional[Dict[str, Any]]:
    if not HELIUS_API_KEY:
        return None

    url = (
        "https://api-mainnet.helius-rpc.com/"
        "?api-key="
        f"{HELIUS_API_KEY}"
    )

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAsset",
        "params": {
            "id": address,
        },
    }

    try:
        async with httpx.AsyncClient(
            timeout=20
        ) as client:
            response = await client.post(
                url,
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

    except Exception as exc:
        print(
            "Helius asset lookup error: "
            f"{exc}"
        )
        return None

    if data.get("error"):
        return None

    return data.get(
        "result"
    )


# ============================================================
# DEX MARKET DATA
# ============================================================

async def get_pair_data(
    chain: str,
    address: str,
) -> Optional[Dict[str, Any]]:
    try:
        pair = await get_best_token_pair(
            chain,
            address,
        )
    except Exception as exc:
        print(
            "DEX pair lookup error "
            f"chain={chain}: {exc}"
        )
        return None

    if not pair:
        return None

    return pair


# ============================================================
# EVM TOKEN VALIDATION
# ============================================================

async def validate_evm_token(
    chain: str,
    address: str,
) -> Dict[str, Any]:
    normalized_address = (
        normalize_evm_address(
            address
        )
    )

    exists = await contract_exists(
        chain,
        normalized_address,
    )

    if not exists:
        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": normalized_address,
            "reason": (
                "No deployed token contract "
                "was found at this address."
            ),
        }

    metadata = (
        await get_erc20_metadata(
            chain,
            normalized_address,
        )
    )

    pair = await get_pair_data(
        chain,
        normalized_address,
    )

    if pair:
        token_name = (
            pair.get("name")
            or metadata.get("name")
            or "Unknown Token"
        )

        token_symbol = (
            pair.get("symbol")
            or metadata.get("symbol")
            or "UNKNOWN"
        )

        return {
            "valid": True,
            "launched": True,
            "launch_status": "LIVE",
            "chain": chain,
            "address": normalized_address,
            "name": token_name,
            "symbol": token_symbol,
            "pair_address": pair.get(
                "pair_address"
            ),
            "dex": pair.get(
                "dex"
            ),
            "dex_url": pair.get(
                "dex_url"
            ),
            "url": pair.get(
                "url"
            ),
            "price_usd": pair.get(
                "price_usd"
            ),
            "market_cap": pair.get(
                "market_cap"
            ),
            "fdv": pair.get(
                "fdv"
            ),
            "liquidity_usd": pair.get(
                "liquidity_usd"
            ),
            "volume_24h": pair.get(
                "volume_24h"
            ),
            "price_change_24h": pair.get(
                "price_change_24h"
            ),
            "buys_24h": pair.get(
                "buys_24h"
            ),
            "sells_24h": pair.get(
                "sells_24h"
            ),
            "pair": pair,
        }

    # Contract exists, but DEX Screener
    # has no live pair yet.
    return {
        "valid": True,
        "launched": False,
        "launch_status": "PRE_LAUNCH",
        "chain": chain,
        "address": normalized_address,
        "name": (
            metadata.get("name")
            or "Unknown Token"
        ),
        "symbol": (
            metadata.get("symbol")
            or "UNKNOWN"
        ),
        "pair_address": None,
        "dex": None,
        "dex_url": None,
        "url": None,
        "price_usd": None,
        "market_cap": None,
        "fdv": None,
        "liquidity_usd": None,
        "volume_24h": None,
        "price_change_24h": None,
        "buys_24h": None,
        "sells_24h": None,
        "pair": None,
    }


# ============================================================
# SOLANA TOKEN VALIDATION
# ============================================================

async def validate_solana_token(
    address: str,
) -> Dict[str, Any]:
    asset = await get_solana_asset(
        address
    )

    # Helius may return no asset for
    # an invalid/nonexistent address.
    if asset is None:
        return {
            "valid": False,
            "launched": False,
            "chain": "solana",
            "address": address,
            "reason": (
                "The Solana token could not "
                "be found."
            ),
        }

    pair = await get_pair_data(
        "solana",
        address,
    )

    content = (
        asset.get("content")
        or {}
    )

    metadata = (
        content.get("metadata")
        or {}
    )

    token_name = (
        metadata.get("name")
        or "Unknown Token"
    )

    token_symbol = (
        metadata.get("symbol")
        or "UNKNOWN"
    )

    if pair:
        token_name = (
            pair.get("name")
            or token_name
        )

        token_symbol = (
            pair.get("symbol")
            or token_symbol
        )

        return {
            "valid": True,
            "launched": True,
            "launch_status": "LIVE",
            "chain": "solana",
            "address": address,
            "name": token_name,
            "symbol": token_symbol,
            "pair_address": pair.get(
                "pair_address"
            ),
            "dex": pair.get(
                "dex"
            ),
            "dex_url": pair.get(
                "dex_url"
            ),
            "url": pair.get(
                "url"
            ),
            "price_usd": pair.get(
                "price_usd"
            ),
            "market_cap": pair.get(
                "market_cap"
            ),
            "fdv": pair.get(
                "fdv"
            ),
            "liquidity_usd": pair.get(
                "liquidity_usd"
            ),
            "volume_24h": pair.get(
                "volume_24h"
            ),
            "price_change_24h": pair.get(
                "price_change_24h"
            ),
            "buys_24h": pair.get(
                "buys_24h"
            ),
            "sells_24h": pair.get(
                "sells_24h"
            ),
            "pair": pair,
        }

    return {
        "valid": True,
        "launched": False,
        "launch_status": "PRE_LAUNCH",
        "chain": "solana",
        "address": address,
        "name": token_name,
        "symbol": token_symbol,
        "pair_address": None,
        "dex": None,
        "dex_url": None,
        "url": None,
        "price_usd": None,
        "market_cap": None,
        "fdv": None,
        "liquidity_usd": None,
        "volume_24h": None,
        "price_change_24h": None,
        "buys_24h": None,
        "sells_24h": None,
        "pair": None,
    }


# ============================================================
# PUBLIC VALIDATOR
# ============================================================

async def validate_token(
    chain: str,
    address: str,
) -> Dict[str, Any]:
    chain = normalize_chain(
        chain
    )

    address = (
        str(address or "")
        .strip()
    )

    if chain not in SUPPORTED_CHAINS:
        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": address,
            "reason": (
                "Unsupported blockchain."
            ),
        }

    if not address:
        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": address,
            "reason": (
                "Token contract address "
                "is required."
            ),
        }

    if not is_valid_address(
        chain,
        address,
    ):
        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": address,
            "reason": (
                "Invalid token address "
                "format for this chain."
            ),
        }

    if chain in EVM_CHAINS:
        return await validate_evm_token(
            chain,
            address,
        )

    if chain == "solana":
        return await validate_solana_token(
            address
        )

    return {
        "valid": False,
        "launched": False,
        "chain": chain,
        "address": address,
        "reason": (
            "Token validation is not "
            "available for this chain."
        ),
    }


# ============================================================
# COMPATIBILITY ALIAS
# ============================================================

async def validate_token_address(
    chain: str,
    address: str,
) -> Dict[str, Any]:
    return await validate_token(
        chain,
        address,
    )
