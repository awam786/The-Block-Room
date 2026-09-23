import re
from typing import Any, Optional

import httpx

from config import (
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    HELIUS_API_KEY,
    ROBINHOOD_RPC_URL,
)

from services.dex import get_best_token_pair


DEX_SUPPORTED_CHAINS = {
    "bnb": "bnb",
    "ethereum": "ethereum",
    "solana": "solana",
    "robinhood": "robinhood",
}


EVM_CHAINS = {
    "bnb": BNB_RPC_URL,
    "ethereum": ETHEREUM_RPC_URL,
    "robinhood": ROBINHOOD_RPC_URL,
}


SOLANA_ADDRESS_PATTERN = re.compile(
    r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
)

EVM_ADDRESS_PATTERN = re.compile(
    r"^0x[a-fA-F0-9]{40}$"
)


ERC20_NAME_SELECTOR = "0x06fdde03"
ERC20_SYMBOL_SELECTOR = "0x95d89b41"


def normalize_chain(chain: str) -> str:
    value = (chain or "").strip().lower()

    aliases = {
        "bsc": "bnb",
        "binance": "bnb",
        "binance smart chain": "bnb",
        "ethereum": "ethereum",
        "eth": "ethereum",
        "sol": "solana",
        "sol": "solana",
        "robinhood": "robinhood",
        "robinhood chain": "robinhood",
        "rh": "robinhood",
    }

    return aliases.get(value, value)


def is_valid_evm_address(address: str) -> bool:
    return bool(
        EVM_ADDRESS_PATTERN.fullmatch(
            (address or "").strip()
        )
    )


def is_valid_solana_address(address: str) -> bool:
    return bool(
        SOLANA_ADDRESS_PATTERN.fullmatch(
            (address or "").strip()
        )
    )


async def rpc_call(
    rpc_url: str,
    method: str,
    params: list[Any],
) -> Optional[Any]:
    try:
        async with httpx.AsyncClient(
            timeout=15
        ) as client:
            response = await client.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params,
                },
            )

            response.raise_for_status()

            data = response.json()

            if "error" in data:
                return None

            return data.get("result")

    except Exception as exc:
        print(
            f"RPC call failed method={method}: {exc}"
        )
        return None


def decode_abi_string(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    raw = value.removeprefix("0x")

    try:
        if len(raw) < 128:
            return None

        offset = int(raw[:64], 16)

        if offset * 2 + 64 > len(raw):
            return None

        length_start = offset * 2

        length = int(
            raw[
                length_start:
                length_start + 64
            ],
            16,
        )

        data_start = (
            length_start + 64
        )

        data_end = (
            data_start + length * 2
        )

        if data_end > len(raw):
            return None

        data = bytes.fromhex(
            raw[data_start:data_end]
        )

        return data.decode(
            "utf-8",
            errors="ignore",
        ).strip("\x00")

    except Exception:
        return None


def decode_abi_bytes32_string(
    value: Optional[str],
) -> Optional[str]:
    if not value:
        return None

    raw = value.removeprefix("0x")

    try:
        data = bytes.fromhex(raw)

        return data.rstrip(
            b"\x00"
        ).decode(
            "utf-8",
            errors="ignore",
        ).strip()

    except Exception:
        return None


async def get_evm_token_metadata(
    chain: str,
    address: str,
) -> dict[str, Optional[str]]:
    rpc_url = EVM_CHAINS.get(chain)

    if not rpc_url:
        return {
            "name": None,
            "symbol": None,
        }

    name_raw = await rpc_call(
        rpc_url,
        "eth_call",
        [
            {
                "to": address,
                "data": ERC20_NAME_SELECTOR,
            },
            "latest",
        ],
    )

    symbol_raw = await rpc_call(
        rpc_url,
        "eth_call",
        [
            {
                "to": address,
                "data": ERC20_SYMBOL_SELECTOR,
            },
            "latest",
        ],
    )

    name = (
        decode_abi_string(name_raw)
        or decode_abi_bytes32_string(name_raw)
    )

    symbol = (
        decode_abi_string(symbol_raw)
        or decode_abi_bytes32_string(symbol_raw)
    )

    return {
        "name": name,
        "symbol": symbol,
    }


async def evm_contract_exists(
    chain: str,
    address: str,
) -> bool:
    rpc_url = EVM_CHAINS.get(chain)

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

    if not code:
        return False

    return (
        isinstance(code, str)
        and code not in {
            "0x",
            "0x0",
        }
    )


async def validate_evm_token(
    chain: str,
    address: str,
) -> dict[str, Any]:
    if not is_valid_evm_address(address):
        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": address,
            "reason": "Invalid EVM contract address.",
        }

    exists = await evm_contract_exists(
        chain,
        address,
    )

    if not exists:
        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": address,
            "reason": (
                "No deployed contract was found "
                "at this address on the selected chain."
            ),
        }

    metadata = await get_evm_token_metadata(
        chain,
        address,
    )

    pair = None

    try:
        pair = await get_best_token_pair(
            chain,
            address,
        )
    except Exception as exc:
        print(
            "DEX lookup failed "
            f"chain={chain} address={address}: "
            f"{exc}"
        )

    if pair:
        return {
            "valid": True,
            "launched": True,
            "chain": chain,
            "address": address,
            "token_name": (
                pair.get("name")
                or metadata.get("name")
                or "Unknown Token"
            ),
            "token_symbol": (
                pair.get("symbol")
                or metadata.get("symbol")
                or "UNKNOWN"
            ),
            "pair": pair,
            "launch_status": "LIVE",
            "reason": "Live trading pair found.",
        }

    return {
        "valid": True,
        "launched": False,
        "chain": chain,
        "address": address,
        "token_name": (
            metadata.get("name")
            or "Unknown Token"
        ),
        "token_symbol": (
            metadata.get("symbol")
            or "UNKNOWN"
        ),
        "pair": None,
        "launch_status": "PRE_LAUNCH",
        "reason": (
            "Contract exists, but no live "
            "DEX trading pair was found yet."
        ),
    }


async def validate_solana_token(
    address: str,
) -> dict[str, Any]:
    if not is_valid_solana_address(address):
        return {
            "valid": False,
            "launched": False,
            "chain": "solana",
            "address": address,
            "reason": "Invalid Solana token address.",
        }

    if not HELIUS_API_KEY:
        return {
            "valid": False,
            "launched": False,
            "chain": "solana",
            "address": address,
            "reason": (
                "Helius API key is not configured."
            ),
        }

    helius_url = (
        "https://mainnet.helius-rpc.com/"
        f"?api-key={HELIUS_API_KEY}"
    )

    asset_result = None

    try:
        async with httpx.AsyncClient(
            timeout=15
        ) as client:
            response = await client.post(
                helius_url,
                json={
                    "jsonrpc": "2.0",
                    "id": "token-validator",
                    "method": "getAsset",
                    "params": {
                        "id": address,
                        "displayOptions": {
                            "showFungible": True,
                        },
                    },
                },
            )

            response.raise_for_status()

            data = response.json()

            if "error" not in data:
                asset_result = data.get(
                    "result"
                )

    except Exception as exc:
        print(
            "Helius token lookup failed: "
            f"{exc}"
        )

    if not asset_result:
        return {
            "valid": False,
            "launched": False,
            "chain": "solana",
            "address": address,
            "reason": (
                "Token could not be verified "
                "on Solana."
            ),
        }

    content = (
        asset_result.get("content")
        or {}
    )

    metadata = (
        content.get("metadata")
        or {}
    )

    token_name = (
        metadata.get("name")
        or asset_result.get("name")
        or "Unknown Token"
    )

    token_symbol = (
        metadata.get("symbol")
        or asset_result.get("symbol")
        or "UNKNOWN"
    )

    pair = None

    try:
        pair = await get_best_token_pair(
            "solana",
            address,
        )
    except Exception as exc:
        print(
            "Solana DEX lookup failed "
            f"address={address}: {exc}"
        )

    if pair:
        return {
            "valid": True,
            "launched": True,
            "chain": "solana",
            "address": address,
            "token_name": (
                pair.get("name")
                or token_name
            ),
            "token_symbol": (
                pair.get("symbol")
                or token_symbol
            ),
            "pair": pair,
            "launch_status": "LIVE",
            "reason": "Live trading pair found.",
        }

    return {
        "valid": True,
        "launched": False,
        "chain": "solana",
        "address": address,
        "token_name": token_name,
        "token_symbol": token_symbol,
        "pair": None,
        "launch_status": "PRE_LAUNCH",
        "reason": (
            "Token exists on Solana, but no "
            "live DEX trading pair was found yet."
        ),
    }


async def validate_token(
    chain: str,
    address: str,
) -> dict[str, Any]:
    chain = normalize_chain(chain)
    address = (address or "").strip()

    if chain not in DEX_SUPPORTED_CHAINS:
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
                "Contract address is required."
            ),
        }

    if chain == "solana":
        return await validate_solana_token(
            address
        )

    return await validate_evm_token(
        chain,
        address,
    )
