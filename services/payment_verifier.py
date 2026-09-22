import httpx

from decimal import Decimal, InvalidOperation

from config import (
    ETHERSCAN_API_KEY,
    HELIUS_API_KEY,
)

from database.connection import get_pool


ETHERSCAN_V2_URL = (
    "https://api.etherscan.io/v2/api"
)

HELIUS_RPC_URL = (
    "https://mainnet.helius-rpc.com/"
)


EVM_USDT_CONTRACTS = {
    "ethereum": (
        "0xdAC17F958D2ee523a2206206994597C13D831ec7"
    ),
    "bnb": (
        "0x55d398326f99059fF775485246999027B3197955"
    ),
}


SOLANA_USDT_MINT = (
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
)


CHAIN_IDS = {
    "ethereum": 1,
    "bnb": 56,
}


USDT_DECIMALS = 6


def decimal_value(value):
    try:
        return Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return Decimal("0")


async def get_order(
    order_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT *
            FROM orders
            WHERE order_id = $1
            LIMIT 1;
            """,
            order_id,
        )


async def get_payment_tolerance(
    connection,
):
    """
    Reads the optional payment tolerance
    from the database.

    If the setting does not exist yet,
    use a safe default of 0.10 USDT.
    """

    try:
        value = await connection.fetchval(
            """
            SELECT value
            FROM system_settings
            WHERE key = 'payment_tolerance_usdt'
            LIMIT 1;
            """
        )

        if value is None:
            return Decimal("0.10")

        return decimal_value(value)

    except Exception:
        return Decimal("0.10")


async def transaction_already_used(
    tx_hash: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT id
            FROM used_payment_hashes
            WHERE LOWER(tx_hash) = LOWER($1)
            LIMIT 1;
            """,
            tx_hash,
        )

        return row is not None


async def save_used_transaction(
    tx_hash: str,
    order_id: str,
    chain: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO used_payment_hashes (
                tx_hash,
                order_id,
                chain,
                created_at
            )
            VALUES (
                $1,
                $2,
                $3,
                NOW()
            )
            ON CONFLICT (
                tx_hash
            )
            DO NOTHING;
            """,
            tx_hash,
            order_id,
            chain,
        )


async def get_evm_token_transfers(
    chain: str,
    tx_hash: str,
):
    if not ETHERSCAN_API_KEY:
        raise RuntimeError(
            "ETHERSCAN_API_KEY is not configured."
        )

    chain_id = CHAIN_IDS.get(
        chain.lower()
    )

    if not chain_id:
        return []

    params = {
        "chainid": chain_id,
        "module": "account",
        "action": "tokentx",
        "txhash": tx_hash,
        "contractaddress": EVM_USDT_CONTRACTS[
            chain.lower()
        ],
        "apikey": ETHERSCAN_API_KEY,
    }

    async with httpx.AsyncClient(
        timeout=20
    ) as client:

        response = await client.get(
            ETHERSCAN_V2_URL,
            params=params,
        )

        response.raise_for_status()

        data = response.json()

    if not isinstance(data, dict):
        return []

    result = data.get(
        "result"
    )

    if not isinstance(
        result,
        list,
    ):
        return []

    return result


def verify_evm_transfer(
    transfers: list,
    receiver: str,
    expected_amount: Decimal,
):
    receiver = (
        receiver
        or ""
    ).lower().strip()

    if not receiver:
        return {
            "valid": False,
            "reason": "Payment receiver is missing.",
        }

    expected_amount = (
        expected_amount
        if expected_amount > 0
        else Decimal("0")
    )

    for transfer in transfers:

        contract = (
            transfer.get(
                "contractAddress"
            )
            or ""
        ).lower()

        if not contract:
            continue

        from_address = (
            transfer.get(
                "from"
            )
            or ""
        ).lower()

        to_address = (
            transfer.get(
                "to"
            )
            or ""
        ).lower()

        if to_address != receiver:
            continue

        raw_value = transfer.get(
            "value"
        )

        try:
            raw_value = int(
                raw_value or 0
            )
        except (
            ValueError,
            TypeError,
        ):
            continue

        amount = (
            Decimal(raw_value)
            / (
                Decimal(10)
                ** USDT_DECIMALS
            )
        )

        return {
            "valid": True,
            "from": from_address,
            "to": to_address,
            "amount": amount,
            "contract": contract,
        }

    return {
        "valid": False,
        "reason": (
            "No matching USDT transfer "
            "to the order payment wallet "
            "was found."
        ),
    }


async def verify_evm_payment(
    order,
    tx_hash: str,
):
    chain = (
        order["chain"]
        or ""
    ).lower()

    receiver = (
        order["payment_wallet"]
        or ""
    ).strip()

    if not receiver:
        return {
            "valid": False,
            "reason": (
                "This order does not have a "
                "saved payment wallet."
            ),
        }

    transfers = (
        await get_evm_token_transfers(
            chain,
            tx_hash,
        )
    )

    expected_amount = decimal_value(
        order["amount"]
    )

    result = verify_evm_transfer(
        transfers,
        receiver,
        expected_amount,
    )

    if not result["valid"]:
        return result

    tolerance = (
        Decimal("0.10")
    )

    actual_amount = result[
        "amount"
    ]

    difference = abs(
        actual_amount
        - expected_amount
    )

    if difference > tolerance:
        return {
            "valid": False,
            "reason": (
                f"Received {actual_amount} "
                f"USDT, but expected "
                f"{expected_amount} USDT."
            ),
        }

    return {
        "valid": True,
        "amount": actual_amount,
        "receiver": receiver,
        "from": result.get(
            "from"
        ),
        "contract": result.get(
            "contract"
        ),
    }


async def helius_rpc(
    method: str,
    params: list,
):
    if not HELIUS_API_KEY:
        raise RuntimeError(
            "HELIUS_API_KEY is not configured."
        )

    payload = {
        "jsonrpc": "2.0",
        "id": "the-block-room",
        "method": method,
        "params": params,
    }

    async with httpx.AsyncClient(
        timeout=20
    ) as client:

        response = await client.post(
            HELIUS_RPC_URL,
            params={
                "api-key": HELIUS_API_KEY,
            },
            json=payload,
        )

        response.raise_for_status()

        data = response.json()

    if data.get("error"):
        raise RuntimeError(
            data["error"].get(
                "message",
                "Helius RPC request failed.",
            )
        )

    return data.get(
        "result"
    )


async def get_solana_transaction(
    tx_hash: str,
):
    return await helius_rpc(
        "getTransaction",
        [
            tx_hash,
            {
                "encoding": "jsonParsed",
                "commitment": "finalized",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )


def verify_solana_usdt_transfer(
    transaction: dict,
    receiver: str,
    expected_amount: Decimal,
):
    if not transaction:
        return {
            "valid": False,
            "reason": "Transaction not found.",
        }

    meta = (
        transaction.get(
            "meta"
        )
        or {}
    )

    if meta.get(
        "err"
    ):
        return {
            "valid": False,
            "reason": "Transaction failed.",
        }

    receiver = (
        receiver
        or ""
    ).strip()

    if not receiver:
        return {
            "valid": False,
            "reason": "Payment receiver is missing.",
        }

    pre_balances = (
        meta.get(
            "preTokenBalances"
        )
        or []
    )

    post_balances = (
        meta.get(
            "postTokenBalances"
        )
        or []
    )

    pre = Decimal("0")
    post = Decimal("0")

    for item in pre_balances:
        if (
            item.get("mint")
            != SOLANA_USDT_MINT
        ):
            continue

        if (
            item.get("owner")
            != receiver
        ):
            continue

        amount = (
            item.get(
                "uiTokenAmount"
            )
            or {}
        )

        pre = decimal_value(
            amount.get(
                "uiAmount"
            )
        )

    for item in post_balances:
        if (
            item.get("mint")
            != SOLANA_USDT_MINT
        ):
            continue

        if (
            item.get("owner")
            != receiver
        ):
            continue

        amount = (
            item.get(
                "uiTokenAmount"
            )
            or {}
        )

        post = decimal_value(
            amount.get(
                "uiAmount"
            )
        )

    received = (
        post - pre
    )

    if received <= 0:
        return {
            "valid": False,
            "reason": (
                "No matching USDT transfer "
                "to the payment wallet "
                "was found."
            ),
        }

    tolerance = Decimal(
        "0.10"
    )

    difference = abs(
        received
        - expected_amount
    )

    if difference > tolerance:
        return {
            "valid": False,
            "reason": (
                f"Received {received} "
                f"USDT, but expected "
                f"{expected_amount} USDT."
            ),
        }

    return {
        "valid": True,
        "amount": received,
        "receiver": receiver,
    }


async def verify_solana_payment(
    order,
    tx_hash: str,
):
    transaction = (
        await get_solana_transaction(
            tx_hash
        )
    )

    return verify_solana_usdt_transfer(
        transaction,
        order["payment_wallet"],
        decimal_value(
            order["amount"]
        ),
    )


async def verify_payment(
    order_id: int,
    tx_hash: str,
):
    tx_hash = (
        tx_hash
        or ""
    ).strip()

    if not tx_hash:
        return {
            "valid": False,
            "reason": (
                "Transaction hash is empty."
            ),
        }

    order = await get_order(
        order_id
    )

    if not order:
        return {
            "valid": False,
            "reason": "Order not found.",
        }

    if order["status"] not in (
        "PAYMENT_SUBMITTED",
        "PAYMENT_PENDING",
    ):
        return {
            "valid": False,
            "reason": (
                "This order is not waiting "
                "for payment verification."
            ),
        }

    if await transaction_already_used(
        tx_hash
    ):
        return {
            "valid": False,
            "reason": (
                "This transaction hash has "
                "already been used."
            ),
        }

    chain = (
        order["chain"]
        or ""
    ).lower()

    try:
        if chain in (
            "ethereum",
            "bnb",
        ):
            result = (
                await verify_evm_payment(
                    order,
                    tx_hash,
                )
            )

        elif chain == "solana":
            result = (
                await verify_solana_payment(
                    order,
                    tx_hash,
                )
            )

        else:
            return {
                "valid": False,
                "reason": (
                    "Payment verification is "
                    "not available for this chain."
                ),
            }

    except Exception as exc:
        print(
            "Payment verification error: "
            f"{exc}"
        )

        return {
            "valid": False,
            "pending": True,
            "reason": (
                "The transaction could not "
                "be verified yet. Please try "
                "again shortly."
            ),
        }

    if not result.get(
        "valid"
    ):
        return result

    await save_used_transaction(
        tx_hash,
        str(
            order_id
        ),
        chain,
    )

    return {
        **result,
        "valid": True,
        "order_id": order_id,
        "tx_hash": tx_hash,
    }
