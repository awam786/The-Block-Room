import re

import httpx

from services.dex import (
    get_token_pairs,
    choose_best_pair,
    parse_pair,
)

from services.helius import (
    get_solana_asset,
)

from config import (
    ROBINHOOD_RPC_URL,
)


# ============================================================
# ADDRESS VALIDATION
# ============================================================

EVM_ADDRESS_REGEX = re.compile(
    r"^0x[a-fA-F0-9]{40}$"
)

SOLANA_ADDRESS_REGEX = re.compile(
    r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
)


def normalize_chain(
    chain: str,
) -> str:
    value = (
        chain or ""
    ).strip().lower()

    aliases = {
        "bsc": "bnb",
        "binance": "bnb",
        "binance smart chain": "bnb",
        "eth": "ethereum",
        "ethereum mainnet": "ethereum",
        "sol": "solana",
        "rh": "robinhood",
        "robinhood chain": "robinhood",
    }

    return aliases.get(
        value,
        value,
    )


def validate_address_format(
    chain: str,
    address: str,
) -> bool:
    chain = normalize_chain(chain)

    address = (
        address or ""
    ).strip()

    if chain in {
        "bnb",
        "ethereum",
        "robinhood",
    }:
        return bool(
            EVM_ADDRESS_REGEX.fullmatch(
                address
            )
        )

    if chain == "solana":
        return bool(
            SOLANA_ADDRESS_REGEX.fullmatch(
                address
            )
        )

    return False


# ============================================================
# ROBINHOOD RPC HELPERS
# ============================================================

async def robinhood_rpc(
    method: str,
    params: list,
):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    timeout = httpx.Timeout(
        15.0,
        connect=10.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout
    ) as client:

        response = await client.post(
            ROBINHOOD_RPC_URL,
            json=payload,
        )

        response.raise_for_status()

        data = response.json()

    if data.get("error"):
        raise RuntimeError(
            str(data["error"])
        )

    return data.get("result")


async def robinhood_contract_exists(
    address: str,
) -> bool:
    try:
        code = await robinhood_rpc(
            "eth_getCode",
            [
                address,
                "latest",
            ],
        )

    except Exception as exc:
        print(
            "Robinhood RPC contract check error: "
            f"{exc}"
        )

        raise

    if not code:
        return False

    return code not in {
        "0x",
        "0x0",
    }


# ============================================================
# EVM ERC-20 METADATA
# ============================================================

ERC20_NAME_SELECTOR = (
    "0x06fdde03"
)

ERC20_SYMBOL_SELECTOR = (
    "0x95d89b41"
)


def decode_abi_string(
    value: str,
) -> str | None:
    if not value:
        return None

    if not value.startswith("0x"):
        return None

    raw = value[2:]

    if not raw:
        return None

    try:
        # Standard dynamic ABI string:
        #
        # offset | length | UTF-8 bytes
        #
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
                    length_position
                    + 64
                )

                end = (
                    start
                    + length * 2
                )

                if end <= len(raw):
                    encoded = raw[
                        start:end
                    ]

                    decoded = bytes.fromhex(
                        encoded
                    ).decode(
                        "utf-8",
                        errors="ignore",
                    )

                    decoded = decoded.strip(
                        "\x00"
                    ).strip()

                    if decoded:
                        return decoded

        # Some tokens return bytes32 instead.
        if len(raw) >= 64:
            encoded = raw[:64]

            decoded = bytes.fromhex(
                encoded
            ).decode(
                "utf-8",
                errors="ignore",
            )

            decoded = decoded.strip(
                "\x00"
            ).strip()

            if decoded:
                return decoded

    except (
        ValueError,
        UnicodeDecodeError,
    ):
        return None

    return None


async def get_evm_token_metadata(
    address: str,
) -> tuple[str | None, str | None]:

    name = None
    symbol = None

    try:
        name_result = await robinhood_rpc(
            "eth_call",
            [
                {
                    "to": address,
                    "data": ERC20_NAME_SELECTOR,
                },
                "latest",
            ],
        )

        name = decode_abi_string(
            name_result
        )

    except Exception:
        pass

    try:
        symbol_result = await robinhood_rpc(
            "eth_call",
            [
                {
                    "to": address,
                    "data": ERC20_SYMBOL_SELECTOR,
                },
                "latest",
            ],
        )

        symbol = decode_abi_string(
            symbol_result
        )

    except Exception:
        pass

    return name, symbol


# ============================================================
# ROBINHOOD TOKEN VALIDATION
# ============================================================

async def validate_robinhood_token(
    contract_address: str,
):
    """
    Robinhood Chain is EVM-compatible.

    We verify that the submitted address contains
    deployed contract bytecode.

    Market/pair discovery is deliberately not guessed
    through another chain's DEX mapping. Until a verified
    Robinhood market-data source is integrated, a valid
    contract is treated as found but not confirmed live.
    """

    try:
        exists = await robinhood_contract_exists(
            contract_address
        )

    except Exception:
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Robinhood Chain RPC is temporarily "
                "unavailable."
            ),
        }

    if not exists:
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "No deployed smart contract was found "
                "at this Robinhood Chain address."
            ),
        }

    name, symbol = (
        await get_evm_token_metadata(
            contract_address
        )
    )

    return {
        "valid": True,
        "launched": False,
        "name": name or "Unknown",
        "symbol": symbol or "Unknown",
        "address": contract_address,
        "pair": None,
        "market_data_available": False,
        "launch_status": "UNKNOWN",
    }


# ============================================================
# SOLANA TOKEN VALIDATION
# ============================================================

async def validate_solana_token(
    contract_address: str,
):
    try:
        asset = await get_solana_asset(
            contract_address
        )

    except Exception as exc:
        print(
            "Solana asset validation error: "
            f"{exc}"
        )

        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Solana token verification service "
                "is temporarily unavailable."
            ),
        }

    if not asset:
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Solana token was not found."
            ),
        }

    pairs = []

    try:
        pairs = await get_token_pairs(
            "solana",
            contract_address,
        )

    except Exception as exc:
        print(
            "Solana DEX lookup error: "
            f"{exc}"
        )

    pair = choose_best_pair(
        pairs
    )

    metadata = (
        asset.get(
            "content",
            {},
        )
        .get(
            "metadata",
            {},
        )
    )

    name = metadata.get(
        "name"
    )

    symbol = metadata.get(
        "symbol"
    )

    if pair:
        market_data = parse_pair(
            pair
        )

        return {
            "valid": True,
            "launched": True,
            "name": (
                market_data.get("name")
                or name
                or "Unknown"
            ),
            "symbol": (
                market_data.get("symbol")
                or symbol
                or "Unknown"
            ),
            "address": contract_address,
            "pair": market_data,
            "market_data_available": True,
            "launch_status": "LIVE",
        }

    return {
        "valid": True,
        "launched": False,
        "name": name or "Unknown",
        "symbol": symbol or "Unknown",
        "address": contract_address,
        "pair": None,
        "market_data_available": False,
        "launch_status": "PRE_LAUNCH",
    }


# ============================================================
# EVM TOKEN VALIDATION
# ============================================================

async def validate_evm_token(
    chain: str,
    contract_address: str,
):
    try:
        pairs = await get_token_pairs(
            chain,
            contract_address,
        )

    except Exception as exc:
        print(
            f"{chain} DEX lookup error: "
            f"{exc}"
        )

        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Token market-data service is "
                "temporarily unavailable."
            ),
        }

    pair = choose_best_pair(
        pairs
    )

    if not pair:
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Token was not found on a supported "
                "DEX pair."
            ),
        }

    market_data = parse_pair(
        pair
    )

    returned_address = (
        market_data.get(
            "address"
        )
        or ""
    )

    if (
        returned_address.lower()
        != contract_address.lower()
    ):
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Token address could not be verified."
            ),
        }

    return {
        "valid": True,
        "launched": True,
        "name": (
            market_data.get("name")
            or "Unknown"
        ),
        "symbol": (
            market_data.get("symbol")
            or "Unknown"
        ),
        "address": contract_address,
        "pair": market_data,
        "market_data_available": True,
        "launch_status": "LIVE",
    }


# ============================================================
# MAIN VALIDATOR
# ============================================================

async def validate_token(
    chain: str,
    contract_address: str,
):
    chain = normalize_chain(
        chain
    )

    contract_address = (
        contract_address or ""
    ).strip()

    if not chain:
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Blockchain network was not specified."
            ),
        }

    if not contract_address:
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Contract address was not provided."
            ),
        }

    # --------------------------------------------------------
    # Address format
    # --------------------------------------------------------

    if not validate_address_format(
        chain,
        contract_address,
    ):
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Invalid contract address format."
            ),
        }

    # --------------------------------------------------------
    # Robinhood
    # --------------------------------------------------------

    if chain == "robinhood":
        return await validate_robinhood_token(
            contract_address
        )

    # --------------------------------------------------------
    # Solana
    # --------------------------------------------------------

    if chain == "solana":
        return await validate_solana_token(
            contract_address
        )

    # --------------------------------------------------------
    # BNB / Ethereum
    # --------------------------------------------------------

    if chain in {
        "bnb",
        "ethereum",
    }:
        return await validate_evm_token(
            chain,
            contract_address,
        )

    # --------------------------------------------------------
    # Unknown chain
    # --------------------------------------------------------

    return {
        "valid": False,
        "launched": False,
        "reason": (
            "Unsupported blockchain network."
        ),
    }
