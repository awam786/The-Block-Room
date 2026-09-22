from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import httpx


ERC20_SELECTOR = {
    "name": "0x06fdde03",
    "symbol": "0x95d89b41",
    "decimals": "0x313ce567",
}


@dataclass
class TokenMetadata:
    address: str
    name: str
    symbol: str
    decimals: int


@dataclass
class PairMetadata:
    pair_address: str
    token0: TokenMetadata
    token1: TokenMetadata


def normalize_address(address: str) -> str:
    return address.lower().strip()


def _decode_uint256(result: str) -> int:
    if not result or result == "0x":
        raise ValueError("Empty uint256 result")

    return int(result, 16)


def _decode_string(result: str) -> str:
    if not result or result == "0x":
        return ""

    raw = result[2:]

    try:
        # Standard ABI dynamic string:
        # offset + length + UTF-8 bytes
        if len(raw) >= 128:
            offset = int(raw[:64], 16) * 2
            length_position = offset
            length = int(
                raw[length_position:length_position + 64],
                16,
            )

            data_start = length_position + 64
            data_end = data_start + (length * 2)

            value = bytes.fromhex(
                raw[data_start:data_end]
            ).decode(
                "utf-8",
                errors="replace",
            )

            return value.strip()

        # Some tokens return bytes32 instead.
        data = bytes.fromhex(raw)

        return data.rstrip(b"\x00").decode(
            "utf-8",
            errors="replace",
        ).strip()

    except (ValueError, UnicodeDecodeError):
        return ""


def _decode_address(result: str) -> str:
    if not result or result == "0x":
        raise ValueError("Empty address result")

    raw = result[2:]

    if len(raw) < 64:
        raise ValueError("Invalid ABI address result")

    return "0x" + raw[-40:]


async def rpc_call(
    rpc_url: str,
    method: str,
    params: list,
):
    payload = {
        "jsonrpc": "2.0",
        "id": "the-block-room",
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
            data["error"].get(
                "message",
                "RPC request failed",
            )
        )

    return data.get("result")


async def get_contract_address(
    rpc_url: str,
    contract_address: str,
    selector: str,
) -> str:
    result = await rpc_call(
        rpc_url,
        "eth_call",
        [
            {
                "to": contract_address,
                "data": selector,
            },
            "latest",
        ],
    )

    return _decode_address(result)


async def get_contract_string(
    rpc_url: str,
    contract_address: str,
    selector: str,
) -> str:
    result = await rpc_call(
        rpc_url,
        "eth_call",
        [
            {
                "to": contract_address,
                "data": selector,
            },
            "latest",
        ],
    )

    return _decode_string(result)


async def get_contract_uint(
    rpc_url: str,
    contract_address: str,
    selector: str,
) -> int:
    result = await rpc_call(
        rpc_url,
        "eth_call",
        [
            {
                "to": contract_address,
                "data": selector,
            },
            "latest",
        ],
    )

    return _decode_uint256(result)


async def get_token_metadata(
    rpc_url: str,
    token_address: str,
) -> Optional[TokenMetadata]:
    token_address = normalize_address(
        token_address
    )

    try:
        name = await get_contract_string(
            rpc_url,
            token_address,
            ERC20_SELECTOR["name"],
        )
    except Exception:
        name = ""

    try:
        symbol = await get_contract_string(
            rpc_url,
            token_address,
            ERC20_SELECTOR["symbol"],
        )
    except Exception:
        symbol = ""

    try:
        decimals = await get_contract_uint(
            rpc_url,
            token_address,
            ERC20_SELECTOR["decimals"],
        )
    except Exception:
        decimals = 18

    if not name:
        name = "Unknown Token"

    if not symbol:
        symbol = "TOKEN"

    if decimals < 0 or decimals > 255:
        decimals = 18

    return TokenMetadata(
        address=token_address,
        name=name,
        symbol=symbol,
        decimals=decimals,
    )


async def get_pair_metadata(
    rpc_url: str,
    pair_address: str,
) -> Optional[PairMetadata]:
    pair_address = normalize_address(
        pair_address
    )

    try:
        token0_address = await get_contract_address(
            rpc_url,
            pair_address,
            "0x0dfe1681",
        )

        token1_address = await get_contract_address(
            rpc_url,
            pair_address,
            "0xd21220a7",
        )
    except Exception as exc:
        print(
            "Pair token lookup failed "
            f"pair={pair_address}: {exc}"
        )
        return None

    token0 = await get_token_metadata(
        rpc_url,
        token0_address,
    )

    token1 = await get_token_metadata(
        rpc_url,
        token1_address,
    )

    if not token0 or not token1:
        return None

    return PairMetadata(
        pair_address=pair_address,
        token0=token0,
        token1=token1,
    )


def find_monitored_token(
    pair: PairMetadata,
    monitored_address: str,
) -> Optional[TokenMetadata]:
    monitored_address = normalize_address(
        monitored_address
    )

    if normalize_address(
        pair.token0.address
    ) == monitored_address:
        return pair.token0

    if normalize_address(
        pair.token1.address
    ) == monitored_address:
        return pair.token1

    return None


def calculate_amount(
    raw_amount: int,
    decimals: int,
) -> Decimal:
    if raw_amount <= 0:
        return Decimal("0")

    return Decimal(raw_amount) / (
        Decimal(10) ** decimals
    )


def is_token_buy(
    token_is_token0: bool,
    amount0_in: int,
    amount1_in: int,
    amount0_out: int,
    amount1_out: int,
) -> bool:
    """
    For a standard V2-style pair:

    token0 BUY:
        token0 comes OUT of the pair.

    token1 BUY:
        token1 comes OUT of the pair.
    """

    if token_is_token0:
        return (
            amount0_out > 0
            and amount1_in > 0
        )

    return (
        amount1_out > 0
        and amount0_in > 0
    )


def get_swap_amounts(
    pair: PairMetadata,
    monitored_address: str,
    amount0_in: int,
    amount1_in: int,
    amount0_out: int,
    amount1_out: int,
):
    monitored_address = normalize_address(
        monitored_address
    )

    token0_is_monitored = (
        normalize_address(
            pair.token0.address
        ) == monitored_address
    )

    token1_is_monitored = (
        normalize_address(
            pair.token1.address
        ) == monitored_address
    )

    if not token0_is_monitored and not token1_is_monitored:
        return None

    is_buy = is_token_buy(
        token_is_token0=token0_is_monitored,
        amount0_in=amount0_in,
        amount1_in=amount1_in,
        amount0_out=amount0_out,
        amount1_out=amount1_out,
    )

    if token0_is_monitored:
        token_received = calculate_amount(
            amount0_out,
            pair.token0.decimals,
        )

        token_sold = calculate_amount(
            amount0_in,
            pair.token0.decimals,
        )

        quote_in = amount1_in
        quote_out = amount1_out
        quote_decimals = pair.token1.decimals
        quote_symbol = pair.token1.symbol

    else:
        token_received = calculate_amount(
            amount1_out,
            pair.token1.decimals,
        )

        token_sold = calculate_amount(
            amount1_in,
            pair.token1.decimals,
        )

        quote_in = amount0_in
        quote_out = amount0_out
        quote_decimals = pair.token0.decimals
        quote_symbol = pair.token0.symbol

    quote_in_amount = calculate_amount(
        quote_in,
        quote_decimals,
    )

    quote_out_amount = calculate_amount(
        quote_out,
        quote_decimals,
    )

    return {
        "is_buy": is_buy,
        "token_received": token_received,
        "token_sold": token_sold,
        "quote_in": quote_in_amount,
        "quote_out": quote_out_amount,
        "quote_symbol": quote_symbol,
    }
