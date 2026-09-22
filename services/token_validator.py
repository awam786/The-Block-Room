import re

from services.dex import (
    get_token_pairs,
    choose_best_pair,
    parse_pair,
)
from services.helius import get_solana_asset


EVM_ADDRESS_REGEX = re.compile(
    r"^0x[a-fA-F0-9]{40}$"
)

SOLANA_ADDRESS_REGEX = re.compile(
    r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
)


def validate_address_format(
    chain: str,
    address: str,
) -> bool:
    if chain in {"bnb", "ethereum"}:
        return bool(EVM_ADDRESS_REGEX.fullmatch(address))

    if chain == "solana":
        return bool(SOLANA_ADDRESS_REGEX.fullmatch(address))

    return False


async def validate_token(
    chain: str,
    contract_address: str,
):
    contract_address = contract_address.strip()

    if not validate_address_format(
        chain,
        contract_address,
    ):
        return {
            "valid": False,
            "launched": False,
            "reason": "Invalid contract address format.",
        }

    # Robinhood is deliberately not treated as a normal
    # blockchain here until its exact supported network/API
    # is defined.
    if chain == "robinhood":
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Robinhood network validation is not configured yet."
            ),
        }

    if chain == "solana":
        asset = await get_solana_asset(contract_address)

        if not asset:
            return {
                "valid": False,
                "launched": False,
                "reason": "Solana token was not found.",
            }

        pairs = await get_token_pairs(
            chain,
            contract_address,
        )

        pair = choose_best_pair(pairs)

        metadata = asset.get("content", {}).get(
            "metadata",
            {},
        )

        name = metadata.get("name")
        symbol = metadata.get("symbol")

        if pair:
            market_data = parse_pair(pair)

            return {
                "valid": True,
                "launched": True,
                "name": market_data.get("name") or name,
                "symbol": market_data.get("symbol") or symbol,
                "address": contract_address,
                "pair": market_data,
            }

        return {
            "valid": True,
            "launched": False,
            "name": name,
            "symbol": symbol,
            "address": contract_address,
            "pair": None,
        }

    # BNB / Ethereum
    pairs = await get_token_pairs(
        chain,
        contract_address,
    )

    pair = choose_best_pair(pairs)

    if not pair:
        return {
            "valid": False,
            "launched": False,
            "reason": (
                "Token was not found on a supported DEX pair."
            ),
        }

    market_data = parse_pair(pair)

    # Make sure the returned pair actually belongs to the
    # submitted token address.
    returned_address = (
        market_data.get("address") or ""
    )

    if returned_address.lower() != contract_address.lower():
        return {
            "valid": False,
            "launched": False,
            "reason": "Token address could not be verified.",
        }

    return {
        "valid": True,
        "launched": True,
        "name": market_data.get("name"),
        "symbol": market_data.get("symbol"),
        "address": contract_address,
        "pair": market_data,
    }
