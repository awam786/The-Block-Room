import base64
import re
from typing import Any, Optional

import httpx

from config import (
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    ROBINHOOD_RPC_URL,
    HELIUS_API_KEY,
)

from services.dex import (
    get_best_token_pair,
)


# ============================================================
# SUPPORTED CHAINS
# ============================================================

SUPPORTED_CHAINS = {
    "bnb",
    "ethereum",
    "solana",
    "robinhood",
}


CHAIN_ALIASES = {
    "bsc": "bnb",
    "binance": "bnb",
    "binance-smart-chain": "bnb",
    "eth": "ethereum",
    "sol": "solana",
    "rh": "robinhood",
    "robinhood-chain": "robinhood",
}


# ============================================================
# ADDRESS PATTERNS
# ============================================================

EVM_ADDRESS_PATTERN = re.compile(
    r"^0x[a-fA-F0-9]{40}$"
)

SOLANA_ADDRESS_PATTERN = re.compile(
    r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
)


# ============================================================
# RPC HELPERS
# ============================================================

def normalize_chain(
    chain: str,
) -> str:
    value = (
        str(chain or "")
        .strip()
        .lower()
    )

    return CHAIN_ALIASES.get(
        value,
        value,
    )


def normalize_address(
    address: str,
) -> str:
    return (
        str(address or "")
        .strip()
    )


async def evm_rpc_call(
    rpc_url: str,
    method: str,
    params: list,
):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    async with httpx.AsyncClient(
        timeout=15
    ) as client:
        response = await client.post(
            rpc_url,
            json=payload,
        )

        response.raise_for_status()

        data = response.json()

    if data.get("error"):
        raise RuntimeError(
            str(data["error"])
        )

    return data.get("result")


# ============================================================
# EVM CONTRACT HELPERS
# ============================================================

async def get_evm_contract_code(
    rpc_url: str,
    address: str,
) -> Optional[str]:
    try:
        result = await evm_rpc_call(
            rpc_url,
            "eth_getCode",
            [
                address,
                "latest",
            ],
        )
    except Exception as exc:
        print(
            "EVM contract-code error "
            f"address={address}: {exc}"
        )
        return None

    return result


def is_deployed_contract(
    code: Optional[str],
) -> bool:
    if not code:
        return False

    return code not in (
        "0x",
        "0x0",
        "0x00",
    )


def decode_abi_string(
    result: Optional[str],
) -> Optional[str]:
    if not result:
        return None

    if not isinstance(
        result,
        str,
    ):
        return None

    if not result.startswith(
        "0x"
    ):
        return None

    try:
        raw = bytes.fromhex(
            result[2:]
        )

        if len(raw) < 64:
            return None

        # Dynamic ABI string:
        # first 32 bytes = offset
        # next 32 bytes = length
        offset = int.from_bytes(
            raw[:32],
            byteorder="big",
        )

        if (
            offset < 0
            or offset + 32 > len(raw)
        ):
            return None

        length = int.from_bytes(
            raw[
                offset:
                offset + 32
            ],
            byteorder="big",
        )

        start = (
            offset + 32
        )

        end = (
            start + length
        )

        if end > len(raw):
            return None

        value = raw[
            start:end
        ].decode(
            "utf-8",
            errors="ignore",
        ).strip()

        return value or None

    except Exception:
        return None


def decode_bytes32_string(
    result: Optional[str],
) -> Optional[str]:
    if not result:
        return None

    if not isinstance(
        result,
        str,
    ):
        return None

    if not result.startswith(
        "0x"
    ):
        return None

    try:
        raw = bytes.fromhex(
            result[2:]
        )

        raw = raw.rstrip(
            b"\x00"
        )

        value = raw.decode(
            "utf-8",
            errors="ignore",
        ).strip()

        return value or None

    except Exception:
        return None


async def read_erc20_metadata(
    rpc_url: str,
    address: str,
) -> tuple[
    Optional[str],
    Optional[str],
]:
    name = None
    symbol = None

    # ERC-20 name()
    name_call = (
        "0x06fdde03"
    )

    # ERC-20 symbol()
    symbol_call = (
        "0x95d89b41"
    )

    try:
        name_result = await evm_rpc_call(
            rpc_url,
            "eth_call",
            [
                {
                    "to": address,
                    "data": name_call,
                },
                "latest",
            ],
        )

        name = (
            decode_abi_string(
                name_result
            )
            or decode_bytes32_string(
                name_result
            )
        )

    except Exception:
        name = None

    try:
        symbol_result = await evm_rpc_call(
            rpc_url,
            "eth_call",
            [
                {
                    "to": address,
                    "data": symbol_call,
                },
                "latest",
            ],
        )

        symbol = (
            decode_abi_string(
                symbol_result
            )
            or decode_bytes32_string(
                symbol_result
            )
        )

    except Exception:
        symbol = None

    return (
        name,
        symbol,
    )


# ============================================================
# SOLANA HELPERS
# ============================================================

async def solana_rpc_call(
    method: str,
    params: list,
):
    if not HELIUS_API_KEY:
        raise RuntimeError(
            "HELIUS_API_KEY is not configured."
        )

    url = (
        "https://mainnet.helius-rpc.com/"
        f"?api-key={HELIUS_API_KEY}"
    )

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    async with httpx.AsyncClient(
        timeout=15
    ) as client:
        response = await client.post(
            url,
            json=payload,
        )

        response.raise_for_status()

        data = response.json()

    if data.get("error"):
        raise RuntimeError(
            str(data["error"])
        )

    return data.get("result")


async def get_solana_token_metadata(
    address: str,
) -> tuple[
    Optional[str],
    Optional[str],
]:
    """
    Try Helius DAS metadata first.

    The validator can still accept a token when
    metadata is unavailable, because the address
    itself may be valid and market data may contain
    the token metadata.
    """

    try:
        result = await solana_rpc_call(
            "getAsset",
            [
                address,
            ],
        )
    except Exception as exc:
        print(
            "Solana metadata error "
            f"address={address}: {exc}"
        )
        return (
            None,
            None,
        )

    if not result:
        return (
            None,
            None,
        )

    content = (
        result.get("content")
        or {}
    )

    metadata = (
        content.get("metadata")
        or {}
    )

    name = metadata.get(
        "name"
    )

    symbol = metadata.get(
        "symbol"
    )

    if name:
        name = str(name).strip()

    if symbol:
        symbol = str(symbol).strip()

    return (
        name or None,
        symbol or None,
    )


# ============================================================
# ADDRESS VALIDATION
# ============================================================

def validate_evm_address(
    address: str,
) -> bool:
    return bool(
        EVM_ADDRESS_PATTERN.fullmatch(
            address
        )
    )


def validate_solana_address(
    address: str,
) -> bool:
    return bool(
        SOLANA_ADDRESS_PATTERN.fullmatch(
            address
        )
    )


# ============================================================
# EVM TOKEN VALIDATION
# ============================================================

async def validate_evm_token(
    chain: str,
    address: str,
) -> dict:
    if chain == "ethereum":
        rpc_url = ETHEREUM_RPC_URL

    elif chain == "bnb":
        rpc_url = BNB_RPC_URL

    elif chain == "robinhood":
        rpc_url = ROBINHOOD_RPC_URL

    else:
        return {
            "valid": False,
            "launched": False,
            "launch_status": "UNSUPPORTED_CHAIN",
            "chain": chain,
            "address": address,
        }

    if not validate_evm_address(
        address
    ):
        return {
            "valid": False,
            "launched": False,
            "launch_status": "INVALID_ADDRESS",
            "chain": chain,
            "address": address,
        }

    try:
        code = await get_evm_contract_code(
            rpc_url,
            address,
        )
    except Exception as exc:
        return {
            "valid": False,
            "launched": False,
            "launch_status": "RPC_ERROR",
            "chain": chain,
            "address": address,
            "error": str(exc),
        }

    if code is None:
        return {
            "valid": False,
            "launched": False,
            "launch_status": "RPC_ERROR",
            "chain": chain,
            "address": address,
        }

    if not is_deployed_contract(
        code
    ):
        return {
            "valid": False,
            "launched": False,
            "launch_status": "CONTRACT_NOT_FOUND",
            "chain": chain,
            "address": address,
        }

    name = None
    symbol = None

    try:
        name, symbol = (
            await read_erc20_metadata(
                rpc_url,
                address,
            )
        )
    except Exception:
        pass

    # --------------------------------------------------------
    # Market / pair detection.
    #
    # This is intentionally used for Robinhood too.
    # A deployed contract without a trading pair is
    # considered valid but pre-launch.
    # --------------------------------------------------------

    pair = None

    try:
        pair = await get_best_token_pair(
            chain,
            address,
        )
    except Exception as exc:
        print(
            "DEX pair lookup error "
            f"chain={chain} "
            f"address={address}: {exc}"
        )

    if pair:
        pair_name = pair.get(
            "name"
        )

        pair_symbol = pair.get(
            "symbol"
        )

        if not name:
            name = pair_name

        if not symbol:
            symbol = pair_symbol

        return {
            "valid": True,
            "launched": True,
            "launch_status": "LIVE",
            "chain": chain,
            "address": address,
            "name": name or "Unknown",
            "symbol": symbol or "UNKNOWN",
            "pair": pair,
            "pair_address": pair.get(
                "pair_address"
            ),
            "dex_url": pair.get(
                "dex_url"
            ),
            "price_usd": pair.get(
                "price_usd",
                0,
            ),
            "market_cap": pair.get(
                "market_cap",
                0,
            ),
            "liquidity_usd": pair.get(
                "liquidity_usd",
                0,
            ),
            "volume_24h": pair.get(
                "volume_24h",
                0,
            ),
        }

    return {
        "valid": True,
        "launched": False,
        "launch_status": "PRE_LAUNCH",
        "chain": chain,
        "address": address,
        "name": name or "Unknown",
        "symbol": symbol or "UNKNOWN",
        "pair": None,
        "pair_address": None,
        "dex_url": None,
        "price_usd": 0,
        "market_cap": 0,
        "liquidity_usd": 0,
        "volume_24h": 0,
    }


# ============================================================
# SOLANA TOKEN VALIDATION
# ============================================================

async def validate_solana_token(
    address: str,
) -> dict:
    if not validate_solana_address(
        address
    ):
        return {
            "valid": False,
            "launched": False,
            "launch_status": "INVALID_ADDRESS",
            "chain": "solana",
            "address": address,
        }

    name = None
    symbol = None

    try:
        name, symbol = (
            await get_solana_token_metadata(
                address
            )
        )
    except Exception:
        pass

    pair = None

    try:
        pair = await get_best_token_pair(
            "solana",
            address,
        )
    except Exception as exc:
        print(
            "Solana DEX pair lookup error "
            f"address={address}: {exc}"
        )

    if pair:
        if not name:
            name = pair.get(
                "name"
            )

        if not symbol:
            symbol = pair.get(
                "symbol"
            )

        return {
            "valid": True,
            "launched": True,
            "launch_status": "LIVE",
            "chain": "solana",
            "address": address,
            "name": name or "Unknown",
            "symbol": symbol or "UNKNOWN",
            "pair": pair,
            "pair_address": pair.get(
                "pair_address"
            ),
            "dex_url": pair.get(
                "dex_url"
            ),
            "price_usd": pair.get(
                "price_usd",
                0,
            ),
            "market_cap": pair.get(
                "market_cap",
                0,
            ),
            "liquidity_usd": pair.get(
                "liquidity_usd",
                0,
            ),
            "volume_24h": pair.get(
                "volume_24h",
                0,
            ),
        }

    # A valid Solana token address without a pair
    # can still be booked as a pre-launch token.
    return {
        "valid": True,
        "launched": False,
        "launch_status": "PRE_LAUNCH",
        "chain": "solana",
        "address": address,
        "name": name or "Unknown",
        "symbol": symbol or "UNKNOWN",
        "pair": None,
        "pair_address": None,
        "dex_url": None,
        "price_usd": 0,
        "market_cap": 0,
        "liquidity_usd": 0,
        "volume_24h": 0,
    }


# ============================================================
# PUBLIC VALIDATOR
# ============================================================

async def validate_token(
    chain: str,
    address: str,
) -> dict:
    chain = normalize_chain(
        chain
    )

    address = normalize_address(
        address
    )

    if chain not in SUPPORTED_CHAINS:
        return {
            "valid": False,
            "launched": False,
            "launch_status": "UNSUPPORTED_CHAIN",
            "chain": chain,
            "address": address,
        }

    if not address:
        return {
            "valid": False,
            "launched": False,
            "launch_status": "INVALID_ADDRESS",
            "chain": chain,
            "address": address,
        }

    if chain == "solana":
        return await validate_solana_token(
            address
        )

    return await validate_evm_token(
        chain,
        address,
    )
