import re
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


EVM_ADDRESS_RE = re.compile(
    r"^0x[a-fA-F0-9]{40}$"
)

SOLANA_ADDRESS_RE = re.compile(
    r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
)


CHAIN_ALIASES = {
    "bsc": "bnb",
    "binance": "bnb",
    "bnb": "bnb",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "rh": "robinhood",
    "robinhood": "robinhood",
}


RPC_URLS = {
    "bnb": BNB_RPC_URL,
    "ethereum": ETHEREUM_RPC_URL,
    "robinhood": ROBINHOOD_RPC_URL,
}


def normalize_chain(
    chain: str,
) -> str:
    return CHAIN_ALIASES.get(
        str(chain).strip().lower(),
        str(chain).strip().lower(),
    )


def is_evm_address(
    address: str,
) -> bool:
    return bool(
        EVM_ADDRESS_RE.fullmatch(
            address.strip()
        )
    )


def is_solana_address(
    address: str,
) -> bool:
    return bool(
        SOLANA_ADDRESS_RE.fullmatch(
            address.strip()
        )
    )


async def rpc_call(
    rpc_url: str,
    method: str,
    params: list,
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
            "RPC call error "
            f"method={method}: {exc}"
        )
        return None


async def evm_contract_exists(
    chain: str,
    address: str,
) -> bool:
    rpc_url = RPC_URLS.get(chain)

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
        and code.startswith("0x")
        and code != "0x"
        and code != "0x0"
    )


async def evm_call(
    chain: str,
    to: str,
    data: str,
) -> Optional[str]:
    rpc_url = RPC_URLS.get(chain)

    if not rpc_url:
        return None

    return await rpc_call(
        rpc_url,
        "eth_call",
        [
            {
                "to": to,
                "data": data,
            },
            "latest",
        ],
    )


def decode_abi_string(
    value: Optional[str],
) -> Optional[str]:
    if not value:
        return None

    if not isinstance(value, str):
        return None

    if not value.startswith("0x"):
        return None

    raw = value[2:]

    try:
        data = bytes.fromhex(raw)

        # Standard dynamic ABI string:
        #
        # offset
        # length
        # bytes
        if len(data) >= 64:
            offset = int.from_bytes(
                data[0:32],
                "big",
            )

            if (
                offset + 32
                <= len(data)
            ):
                length = int.from_bytes(
                    data[
                        offset:
                        offset + 32
                    ],
                    "big",
                )

                start = (
                    offset + 32
                )
                end = start + length

                if end <= len(data):
                    return (
                        data[start:end]
                        .decode(
                            "utf-8",
                            errors="ignore",
                        )
                        .strip("\x00")
                    )

        # bytes32 fallback
        if len(data) >= 32:
            return (
                data[:32]
                .rstrip(b"\x00")
                .decode(
                    "utf-8",
                    errors="ignore",
                )
                .strip()
            )

    except Exception:
        return None

    return None


async def get_evm_token_metadata(
    chain: str,
    address: str,
) -> Dict[str, Optional[str]]:
    # ERC-20 name()
    name_result = await evm_call(
        chain,
        address,
        "0x06fdde03",
    )

    # ERC-20 symbol()
    symbol_result = await evm_call(
        chain,
        address,
        "0x95d89b41",
    )

    return {
        "name": decode_abi_string(
            name_result
        ),
        "symbol": decode_abi_string(
            symbol_result
        ),
    }


async def get_solana_asset(
    address: str,
) -> Dict[str, Any]:
    if not HELIUS_API_KEY:
        return {}

    url = (
        "https://mainnet.helius-rpc.com/"
        f"?api-key={HELIUS_API_KEY}"
    )

    payload = {
        "jsonrpc": "2.0",
        "id": "block-room",
        "method": "getAsset",
        "params": {
            "id": address,
            "displayOptions": {
                "showFungible": True,
            },
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

            return data.get(
                "result",
                {},
            )

    except Exception as exc:
        print(
            "Helius asset lookup error: "
            f"{exc}"
        )
        return {}


def extract_solana_metadata(
    asset: Dict[str, Any],
) -> Dict[str, Optional[str]]:
    content = asset.get(
        "content",
        {},
    )

    metadata = content.get(
        "metadata",
        {}
    )

    token_info = asset.get(
        "token_info",
        {}
    )

    name = (
        metadata.get("name")
        or token_info.get("symbol")
    )

    symbol = (
        metadata.get("symbol")
        or token_info.get("symbol")
    )

    return {
        "name": name,
        "symbol": symbol,
    }


async def validate_token(
    chain: str,
    contract_address: str,
) -> Dict[str, Any]:
    chain = normalize_chain(chain)

    contract_address = (
        str(contract_address)
        .strip()
    )

    if chain not in {
        "bnb",
        "ethereum",
        "solana",
        "robinhood",
    }:
        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": contract_address,
            "reason": "Unsupported chain.",
        }

    # ---------------------------------------------------------
    # SOLANA
    # ---------------------------------------------------------

    if chain == "solana":
        if not is_solana_address(
            contract_address
        ):
            return {
                "valid": False,
                "launched": False,
                "chain": chain,
                "address": contract_address,
                "reason": "Invalid Solana token address.",
            }

        asset = await get_solana_asset(
            contract_address
        )

        metadata = extract_solana_metadata(
            asset
        )

        pair = await get_best_token_pair(
            "solana",
            contract_address,
        )

        if pair:
            return {
                "valid": True,
                "launched": True,
                "chain": chain,
                "address": contract_address,
                "name": (
                    pair.get("name")
                    or metadata.get("name")
                    or "Unknown"
                ),
                "symbol": (
                    pair.get("symbol")
                    or metadata.get("symbol")
                    or "UNKNOWN"
                ),
                "pair": pair,
                "market_data": pair,
                "launch_status": "LIVE",
            }

        # Helius confirmed that the asset exists.
        if asset:
            return {
                "valid": True,
                "launched": False,
                "chain": chain,
                "address": contract_address,
                "name": (
                    metadata.get("name")
                    or "Unknown"
                ),
                "symbol": (
                    metadata.get("symbol")
                    or "UNKNOWN"
                ),
                "pair": None,
                "market_data": None,
                "launch_status": "PRE_LAUNCH",
            }

        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": contract_address,
            "reason": (
                "Solana token could not be verified."
            ),
        }

    # ---------------------------------------------------------
    # EVM CHAINS
    # ---------------------------------------------------------

    if not is_evm_address(
        contract_address
    ):
        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": contract_address,
            "reason": "Invalid EVM contract address.",
        }

    exists = await evm_contract_exists(
        chain,
        contract_address,
    )

    if not exists:
        return {
            "valid": False,
            "launched": False,
            "chain": chain,
            "address": contract_address,
            "reason": (
                "No deployed contract was found "
                "at this address."
            ),
        }

    metadata = await get_evm_token_metadata(
        chain,
        contract_address,
    )

    # ---------------------------------------------------------
    # LIVE PAIR CHECK
    #
    # DEX Screener now supports:
    # BNB       -> bsc
    # Ethereum  -> ethereum
    # Robinhood -> robinhood
    # ---------------------------------------------------------

    pair = await get_best_token_pair(
        chain,
        contract_address,
    )

    if pair:
        return {
            "valid": True,
            "launched": True,
            "chain": chain,
            "address": contract_address,
            "name": (
                pair.get("name")
                or metadata.get("name")
                or "Unknown"
            ),
            "symbol": (
                pair.get("symbol")
                or metadata.get("symbol")
                or "UNKNOWN"
            ),
            "pair": pair,
            "market_data": pair,
            "launch_status": "LIVE",
        }

    # ---------------------------------------------------------
    # VALID CONTRACT, NO LIVE PAIR
    #
    # This is important for pre-launch booking.
    # The order can be paid now and monitored later.
    # ---------------------------------------------------------

    return {
        "valid": True,
        "launched": False,
        "chain": chain,
        "address": contract_address,
        "name": (
            metadata.get("name")
            or "Unknown"
        ),
        "symbol": (
            metadata.get("symbol")
            or "UNKNOWN"
        ),
        "pair": None,
        "market_data": None,
        "launch_status": "PRE_LAUNCH",
    }
