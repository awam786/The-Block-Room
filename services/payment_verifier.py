from decimal import Decimal, InvalidOperation

import httpx

from config import (
    ETHERSCAN_API_KEY,
    ETHEREUM_RPC_URL,
    BNB_RPC_URL,
    HELIUS_API_KEY,
)

from database.connection import get_pool


ETH_USDT_CONTRACT = (
    "0xdAC17F958D2ee523a2206206994597C13D831ec7"
)

BNB_USDT_CONTRACT = (
    "0x55d398326f99059fF775485246999027B3197955"
)

SOLANA_USDT_MINT = (
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
)

ETHERSCAN_V2_URL = (
    "https://api.etherscan.io/v2/api"
)

SOLANA_RPC_URL = (
    "https://mainnet.helius-rpc.com/"
)

EVM_CONFIRMATIONS_REQUIRED = 3


def decimal_value(
    value,
    default=Decimal("0"),
):
    try:
        return Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return default


async def get_payment_tolerance():
    pool = await get_pool()

    async with pool.acquire() as connection:
        value = await connection.fetchval(
            """
            SELECT setting_value
            FROM system_settings
            WHERE setting_key =
                'payment_tolerance_usdt'
            LIMIT 1;
            """
        )

    tolerance = decimal_value(
        value,
        Decimal("0.10"),
    )

    if tolerance < 0:
        return Decimal("0.10")

    return tolerance


async def get_order(
    order_id: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT
                order_id,
                user_id,
                chain,
                token_address,
                amount,
                payment_wallet,
                transaction_hash,
                status
            FROM orders
            WHERE order_id = $1
            LIMIT 1;
            """,
            order_id,
        )


async def transaction_already_used(
    tx_hash: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT id
            FROM used_payment_hashes
            WHERE LOWER(tx_hash) =
                  LOWER($1)
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
                chain
            )
            VALUES (
                $1,
                $2,
                $3
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


def get_evm_config(
    chain: str,
):
    normalized = (
        chain
        or ""
    ).lower().strip()

    if normalized == "ethereum":
        return {
            "chain_id": 1,
            "rpc_url": ETHEREUM_RPC_URL,
            "usdt": ETH_USDT_CONTRACT,
        }

    if normalized == "bnb":
        return {
            "chain_id": 56,
            "rpc_url": BNB_RPC_URL,
            "usdt": BNB_USDT_CONTRACT,
        }

    return None


async def etherscan_request(
    chain_id: int,
    params: dict,
):
    request_params = {
        "chainid": chain_id,
        "apikey": ETHERSCAN_API_KEY,
        **params,
    }

    try:
        async with httpx.AsyncClient(
            timeout=20
        ) as client:
            response = await client.get(
                ETHERSCAN_V2_URL,
                params=request_params,
            )

            response.raise_for_status()

            return response.json()

    except (
        httpx.HTTPError,
        ValueError,
    ) as exc:
        return {
            "error": str(exc)
        }


async def rpc_call(
    rpc_url: str,
    method: str,
    params: list,
):
    payload = {
        "jsonrpc": "2.0",
        "id": "payment-verifier",
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

    except (
        httpx.HTTPError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            f"RPC request failed: {exc}"
        ) from exc

    if data.get("error"):
        raise RuntimeError(
            data["error"].get(
                "message",
                "RPC request failed.",
            )
        )

    return data.get("result")


def normalize_address(
    address: str,
):
    return (
        address
        or ""
    ).strip().lower()


async def verify_evm_transfer(
    chain: str,
    tx_hash: str,
    expected_wallet: str,
    expected_amount: Decimal,
):
    configuration = get_evm_config(
        chain
    )

    if not configuration:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Unsupported EVM payment chain."
            ),
        }

    chain_id = configuration[
        "chain_id"
    ]

    rpc_url = configuration[
        "rpc_url"
    ]

    expected_token = normalize_address(
        configuration["usdt"]
    )

    expected_wallet = normalize_address(
        expected_wallet
    )

    # ---------------------------------------------------------
    # GET TOKEN TRANSFERS
    # ---------------------------------------------------------

    data = await etherscan_request(
        chain_id,
        {
            "module": "account",
            "action": "tokentx",
            "contractaddress": (
                configuration["usdt"]
            ),
            "address": expected_wallet,
            "page": 1,
            "offset": 100,
            "sort": "desc",
        },
    )

    if "error" in data:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Blockchain explorer temporarily "
                "unavailable. Payment will be "
                "checked again automatically."
            ),
        }

    transfers = data.get(
        "result"
    )

    if not isinstance(
        transfers,
        list,
    ):
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Payment transaction data is "
                "temporarily unavailable."
            ),
        }

    matching_transfer = None

    for transfer in transfers:
        transfer_hash = (
            transfer.get(
                "hash"
            )
            or ""
        ).lower()

        if transfer_hash != tx_hash.lower():
            continue

        contract = normalize_address(
            transfer.get(
                "contractAddress"
            )
        )

        if contract != expected_token:
            continue

        to_address = normalize_address(
            transfer.get(
                "to"
            )
        )

        if to_address != expected_wallet:
            continue

        matching_transfer = transfer

        break

    if not matching_transfer:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "No matching USDT transfer to "
                "the configured payment address "
                "was found for this transaction."
            ),
        }

    # ---------------------------------------------------------
    # CHECK TRANSACTION RECEIPT
    # ---------------------------------------------------------

    try:
        receipt = await rpc_call(
            rpc_url,
            "eth_getTransactionReceipt",
            [tx_hash],
        )

    except Exception:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Transaction receipt is temporarily "
                "unavailable. Please wait while "
                "the payment is confirmed."
            ),
        }

    if not receipt:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Transaction has not received "
                "a blockchain receipt yet."
            ),
        }

    receipt_status = receipt.get(
        "status"
    )

    if receipt_status != "0x1":
        if receipt_status == "0x0":
            return {
                "valid": False,
                "pending": False,
                "reason": (
                    "The blockchain transaction "
                    "failed."
                ),
            }

        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Transaction status is still "
                "being confirmed."
            ),
        }

    # ---------------------------------------------------------
    # CHECK CONFIRMATIONS
    # ---------------------------------------------------------

    try:
        current_block_hex = (
            await rpc_call(
                rpc_url,
                "eth_blockNumber",
                [],
            )
        )

        current_block = int(
            current_block_hex,
            16,
        )

        transaction_block = int(
            matching_transfer.get(
                "blockNumber",
                "0",
            )
        )

    except (
        ValueError,
        TypeError,
        RuntimeError,
    ):
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Unable to verify transaction "
                "confirmations yet."
            ),
        }

    confirmations = (
        current_block
        - transaction_block
        + 1
    )

    if confirmations < EVM_CONFIRMATIONS_REQUIRED:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Payment transaction is confirmed "
                "but is waiting for additional "
                "block confirmations."
            ),
        }

    # ---------------------------------------------------------
    # CHECK AMOUNT
    # ---------------------------------------------------------

    raw_value = decimal_value(
        matching_transfer.get(
            "value"
        )
    )

    decimals = int(
        matching_transfer.get(
            "tokenDecimal",
            6,
        )
    )

    actual_amount = (
        raw_value
        / (
            Decimal("10")
            ** decimals
        )
    )

    tolerance = (
        await get_payment_tolerance()
    )

    minimum_amount = (
        expected_amount
        - tolerance
    )

    if actual_amount < minimum_amount:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                f"Received {actual_amount} USDT, "
                f"but {expected_amount} USDT "
                f"is required."
            ),
        }

    return {
        "valid": True,
        "pending": False,
        "amount": actual_amount,
        "confirmations": confirmations,
        "reason": "Payment verified.",
    }


async def verify_solana_transfer(
    tx_hash: str,
    expected_wallet: str,
    expected_amount: Decimal,
):
    if not HELIUS_API_KEY:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Solana payment verification "
                "is temporarily unavailable."
            ),
        }

    payload = {
        "jsonrpc": "2.0",
        "id": "payment-verifier",
        "method": "getTransaction",
        "params": [
            tx_hash,
            {
                "encoding": "jsonParsed",
                "commitment": "finalized",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    }

    try:
        async with httpx.AsyncClient(
            timeout=20
        ) as client:
            response = await client.post(
                SOLANA_RPC_URL,
                params={
                    "api-key": HELIUS_API_KEY,
                },
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

    except (
        httpx.HTTPError,
        ValueError,
    ):
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Solana RPC is temporarily "
                "unavailable."
            ),
        }

    if data.get("error"):
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Solana transaction lookup "
                "temporarily failed."
            ),
        }

    transaction = data.get(
        "result"
    )

    if not transaction:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Solana transaction has not "
                "been finalized yet."
            ),
        }

    meta = transaction.get(
        "meta"
    ) or {}

    if meta.get("err") is not None:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "The Solana transaction failed."
            ),
        }

    expected_wallet = (
        expected_wallet
        or ""
    ).strip()

    post_balances = (
        meta.get(
            "postTokenBalances"
        )
        or []
    )

    pre_balances = (
        meta.get(
            "preTokenBalances"
        )
        or []
    )

    pre_map = {}

    for balance in pre_balances:
        key = (
            balance.get(
                "accountIndex"
            ),
            balance.get(
                "mint"
            ),
        )

        pre_map[key] = decimal_value(
            (
                balance.get(
                    "uiTokenAmount"
                )
                or {}
            ).get(
                "uiAmountString"
            )
        )

    matching_received = Decimal("0")

    for balance in post_balances:
        mint = (
            balance.get(
                "mint"
            )
            or ""
        )

        if mint != SOLANA_USDT_MINT:
            continue

        owner = (
            balance.get(
                "owner"
            )
            or ""
        )

        if owner != expected_wallet:
            continue

        key = (
            balance.get(
                "accountIndex"
            ),
            mint,
        )

        post_amount = decimal_value(
            (
                balance.get(
                    "uiTokenAmount"
                )
                or {}
            ).get(
                "uiAmountString"
            )
        )

        previous_amount = (
            pre_map.get(
                key,
                Decimal("0"),
            )
        )

        increase = (
            post_amount
            - previous_amount
        )

        if increase > matching_received:
            matching_received = increase

    tolerance = (
        await get_payment_tolerance()
    )

    minimum_amount = (
        expected_amount
        - tolerance
    )

    if matching_received < minimum_amount:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                f"Received {matching_received} "
                "USDT on Solana, but "
                f"{expected_amount} USDT is required."
            ),
        }

    return {
        "valid": True,
        "pending": False,
        "amount": matching_received,
        "reason": "Payment verified.",
    }


async def verify_payment(
    order_id: str,
    tx_hash: str,
):
    if not order_id:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Invalid order ID."
            ),
        }

    if not tx_hash:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Transaction hash is required."
            ),
        }

    order = await get_order(
        order_id
    )

    if not order:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Order was not found."
            ),
        }

    if order["status"] not in {
        "PAYMENT_SUBMITTED",
        "PAYMENT_PENDING",
    }:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This order is no longer "
                "awaiting payment verification."
            ),
        }

    already_used = (
        await transaction_already_used(
            tx_hash
        )
    )

    if already_used:
        existing_hash = (
            order.get(
                "transaction_hash"
            )
            or ""
        )

        if existing_hash.lower() != (
            tx_hash.lower()
        ):
            return {
                "valid": False,
                "pending": False,
                "reason": (
                    "This transaction hash has "
                    "already been used for another "
                    "order."
                ),
            }

    chain = (
        order["chain"]
        or ""
    ).lower().strip()

    expected_amount = decimal_value(
        order["amount"]
    )

    payment_wallet = (
        order["payment_wallet"]
        or ""
    ).strip()

    if not payment_wallet:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "No payment wallet is configured "
                "for this order."
            ),
        }

    if chain in {
        "ethereum",
        "bnb",
    }:
        result = await verify_evm_transfer(
            chain=chain,
            tx_hash=tx_hash,
            expected_wallet=payment_wallet,
            expected_amount=expected_amount,
        )

    elif chain == "solana":
        result = await verify_solana_transfer(
            tx_hash=tx_hash,
            expected_wallet=payment_wallet,
            expected_amount=expected_amount,
        )

    else:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This payment chain is not "
                "supported."
            ),
        }

    if result.get(
        "valid"
    ):
        await save_used_transaction(
            tx_hash=tx_hash,
            order_id=order_id,
            chain=chain,
        )

    return result
