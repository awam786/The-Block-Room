import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import httpx

from config import (
    BNB_RPC_URL,
    ETHEREUM_RPC_URL,
    ETHERSCAN_API_KEY,
    HELIUS_API_KEY,
    ROBINHOOD_RPC_URL,
)

from database.connection import get_pool


# ============================================================
# PAYMENT CONFIGURATION
# ============================================================

ETHEREUM_USDT_CONTRACT = (
    "0xdac17f958d2ee523a2206206994597c13d831ec7"
).lower()

BNB_USDT_CONTRACT = (
    "0x55d398326f99059ff775485246999027b3197955"
).lower()

# Official USDT SPL mint on Solana.
SOLANA_USDT_MINT = (
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
).lower()


EVM_DECIMALS = 6
SOLANA_DECIMALS = 6

DEFAULT_CONFIRMATIONS = 3
DEFAULT_TOLERANCE_USDT = Decimal("0.10")

HTTP_TIMEOUT = 20.0


# ============================================================
# GENERAL HELPERS
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


def normalize_address(
    address: Optional[str],
) -> str:
    return (
        str(address or "")
        .strip()
        .lower()
    )


def decimal_value(
    value: Any,
) -> Optional[Decimal]:
    if value is None:
        return None

    try:
        return Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return None


def within_tolerance(
    actual: Decimal,
    expected: Decimal,
    tolerance: Decimal,
) -> bool:
    return abs(
        actual - expected
    ) <= tolerance


# ============================================================
# SYSTEM SETTINGS
# ============================================================

async def get_system_setting(
    key: str,
) -> Optional[str]:
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT value
            FROM system_settings
            WHERE key = $1
            LIMIT 1;
            """,
            key,
        )

    if not row:
        return None

    return row["value"]


async def get_payment_tolerance() -> Decimal:
    value = await get_system_setting(
        "payment_tolerance_usdt"
    )

    parsed = decimal_value(
        value
    )

    if parsed is None:
        return DEFAULT_TOLERANCE_USDT

    if parsed < 0:
        return DEFAULT_TOLERANCE_USDT

    return parsed


# ============================================================
# ORDER LOOKUP
# ============================================================

async def get_order_for_payment(
    order_id: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT
                id,
                order_id,
                user_id,
                chain,
                token_address,
                token_name,
                token_symbol,
                duration_hours,
                amount,
                payment_wallet,
                transaction_hash,
                status,
                payment_error,
                created_at,
                updated_at,
                paid_at,
                activated_at,
                expires_at
            FROM orders
            WHERE order_id = $1
            LIMIT 1;
            """,
            order_id,
        )

    return row


# ============================================================
# DUPLICATE TRANSACTION PROTECTION
# ============================================================

async def transaction_hash_used(
    transaction_hash: str,
) -> bool:
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT id
            FROM used_payment_hashes
            WHERE transaction_hash = $1
            LIMIT 1;
            """,
            transaction_hash.strip(),
        )

    return row is not None


async def reserve_transaction_hash(
    transaction_hash: str,
    order_id: str,
) -> bool:
    pool = await get_pool()

    async with pool.acquire() as connection:
        try:
            await connection.execute(
                """
                INSERT INTO used_payment_hashes (
                    transaction_hash,
                    order_id
                )
                VALUES ($1, $2);
                """,
                transaction_hash.strip(),
                order_id,
            )
            return True

        except Exception as exc:
            message = str(exc).lower()

            if (
                "duplicate"
                in message
                or "unique"
                in message
            ):
                return False

            raise


# ============================================================
# RPC HELPERS
# ============================================================

async def rpc_call(
    rpc_url: str,
    method: str,
    params: list,
) -> Optional[Dict[str, Any]]:
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
            timeout=HTTP_TIMEOUT
        ) as client:
            response = await client.post(
                rpc_url,
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

    except Exception as exc:
        print(
            f"RPC request error "
            f"method={method}: {exc}"
        )
        return None

    if data.get("error"):
        print(
            f"RPC returned error "
            f"method={method}: "
            f"{data['error']}"
        )
        return None

    return data


# ============================================================
# EVM RECEIPT
# ============================================================

async def get_evm_receipt(
    rpc_url: str,
    transaction_hash: str,
) -> Optional[Dict[str, Any]]:
    result = await rpc_call(
        rpc_url,
        "eth_getTransactionReceipt",
        [transaction_hash],
    )

    if not result:
        return None

    return result.get(
        "result"
    )


async def get_evm_block_number(
    rpc_url: str,
) -> Optional[int]:
    result = await rpc_call(
        rpc_url,
        "eth_blockNumber",
        [],
    )

    if not result:
        return None

    value = result.get(
        "result"
    )

    if not value:
        return None

    try:
        return int(
            value,
            16,
        )
    except (
        ValueError,
        TypeError,
    ):
        return None


# ============================================================
# ETHERSCAN TOKEN TRANSFERS
# ============================================================

async def get_etherscan_token_transfers(
    chain_id: int,
    transaction_hash: str,
) -> Optional[list]:
    if not ETHERSCAN_API_KEY:
        return None

    url = (
        "https://api.etherscan.io/v2/api"
    )

    params = {
        "chainid": str(chain_id),
        "module": "account",
        "action": "tokentx",
        "txhash": transaction_hash,
        "apikey": ETHERSCAN_API_KEY,
    }

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT
        ) as client:
            response = await client.get(
                url,
                params=params,
            )

            response.raise_for_status()

            data = response.json()

    except Exception as exc:
        print(
            "Etherscan token transfer "
            f"request error: {exc}"
        )
        return None

    result = data.get(
        "result"
    )

    if not isinstance(
        result,
        list,
    ):
        return []

    return result


# ============================================================
# EVM PAYMENT VERIFICATION
# ============================================================

async def verify_evm_payment(
    order,
    transaction_hash: str,
    chain: str,
) -> Dict[str, Any]:
    if chain == "bnb":
        rpc_url = BNB_RPC_URL
        chain_id = 56
        expected_token = BNB_USDT_CONTRACT

    elif chain == "ethereum":
        rpc_url = ETHEREUM_RPC_URL
        chain_id = 1
        expected_token = (
            ETHEREUM_USDT_CONTRACT
        )

    else:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Unsupported EVM payment chain."
            ),
        }

    payment_wallet = normalize_address(
        order["payment_wallet"]
    )

    if not payment_wallet:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Payment wallet is not configured."
            ),
        }

    transaction_hash = (
        transaction_hash.strip()
    )

    if await transaction_hash_used(
        transaction_hash
    ):
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This transaction hash "
                "has already been used."
            ),
        }

    receipt = await get_evm_receipt(
        rpc_url,
        transaction_hash,
    )

    if receipt is None:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Transaction has not "
                "been confirmed on-chain yet."
            ),
        }

    receipt_status = receipt.get(
        "status"
    )

    if receipt_status != "0x1":
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "The blockchain transaction "
                "failed."
            ),
        }

    transfers = (
        await get_etherscan_token_transfers(
            chain_id,
            transaction_hash,
        )
    )

    if transfers is None:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Unable to retrieve token "
                "transfer information yet."
            ),
        }

    expected_amount = decimal_value(
        order["amount"]
    )

    if expected_amount is None:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Invalid order payment amount."
            ),
        }

    tolerance = (
        await get_payment_tolerance()
    )

    matching_transfer = None

    for transfer in transfers:
        token_address = normalize_address(
            transfer.get(
                "contractAddress"
            )
        )

        if token_address != expected_token:
            continue

        to_address = normalize_address(
            transfer.get("to")
        )

        if to_address != payment_wallet:
            continue

        decimals = int(
            transfer.get(
                "tokenDecimal",
                EVM_DECIMALS,
            )
            or EVM_DECIMALS
        )

        raw_value = transfer.get(
            "value"
        )

        if raw_value is None:
            continue

        try:
            actual_amount = (
                Decimal(str(raw_value))
                / (
                    Decimal(10)
                    ** decimals
                )
            )
        except (
            InvalidOperation,
            ValueError,
        ):
            continue

        if not within_tolerance(
            actual_amount,
            expected_amount,
            tolerance,
        ):
            continue

        matching_transfer = {
            "amount": actual_amount,
            "from": transfer.get(
                "from"
            ),
            "to": transfer.get(
                "to"
            ),
            "token": token_address,
        }
        break

    if matching_transfer is None:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "No matching USDT transfer "
                "to the configured payment "
                "wallet was found."
            ),
        }

    latest_block = (
        await get_evm_block_number(
            rpc_url
        )
    )

    transaction_block = receipt.get(
        "blockNumber"
    )

    if (
        latest_block is None
        or transaction_block is None
    ):
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Waiting for blockchain "
                "confirmation information."
            ),
        }

    try:
        transaction_block_number = int(
            transaction_block,
            16,
        )
    except (
        ValueError,
        TypeError,
    ):
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Unable to determine "
                "transaction confirmation."
            ),
        }

    confirmations = (
        latest_block
        - transaction_block_number
        + 1
    )

    if confirmations < DEFAULT_CONFIRMATIONS:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                f"Waiting for confirmations "
                f"({confirmations}/"
                f"{DEFAULT_CONFIRMATIONS})."
            ),
        }

    reserved = (
        await reserve_transaction_hash(
            transaction_hash,
            order["order_id"],
        )
    )

    if not reserved:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This transaction hash "
                "has already been used."
            ),
        }

    return {
        "valid": True,
        "pending": False,
        "amount": matching_transfer[
            "amount"
        ],
        "confirmations": confirmations,
        "from": matching_transfer[
            "from"
        ],
        "to": matching_transfer[
            "to"
        ],
        "token": matching_transfer[
            "token"
        ],
    }


# ============================================================
# HELIUS SOLANA TRANSACTION
# ============================================================

async def get_helius_transaction(
    transaction_hash: str,
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
        "method": "getTransaction",
        "params": [
            transaction_hash,
            {
                "encoding": "jsonParsed",
                "commitment": "finalized",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    }

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT
        ) as client:
            response = await client.post(
                url,
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

    except Exception as exc:
        print(
            "Helius transaction request "
            f"error: {exc}"
        )
        return None

    if data.get("error"):
        print(
            "Helius transaction error: "
            f"{data['error']}"
        )
        return None

    return data.get(
        "result"
    )


# ============================================================
# SOLANA PAYMENT VERIFICATION
# ============================================================

async def verify_solana_payment(
    order,
    transaction_hash: str,
) -> Dict[str, Any]:
    if not HELIUS_API_KEY:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Solana payment verification "
                "is not configured."
            ),
        }

    payment_wallet = (
        order["payment_wallet"]
        or ""
    ).strip()

    if not payment_wallet:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Solana payment wallet "
                "is not configured."
            ),
        }

    transaction_hash = (
        transaction_hash.strip()
    )

    if await transaction_hash_used(
        transaction_hash
    ):
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This transaction hash "
                "has already been used."
            ),
        }

    transaction = (
        await get_helius_transaction(
            transaction_hash
        )
    )

    if transaction is None:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Transaction has not reached "
                "finalized status yet."
            ),
        }

    meta = transaction.get(
        "meta"
    )

    if not meta:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Transaction metadata is "
                "not available yet."
            ),
        }

    if meta.get("err") is not None:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "The Solana transaction failed."
            ),
        }

    expected_amount = decimal_value(
        order["amount"]
    )

    if expected_amount is None:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Invalid order payment amount."
            ),
        }

    tolerance = (
        await get_payment_tolerance()
    )

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

    pre_by_account = {}

    for balance in pre_balances:
        account_index = balance.get(
            "accountIndex"
        )

        if account_index is None:
            continue

        pre_by_account[
            account_index
        ] = balance

    matching_amount = Decimal("0")
    sender_owner = None

    for post in post_balances:
        mint = (
            str(
                post.get("mint")
                or ""
            )
            .strip()
            .lower()
        )

        if mint != SOLANA_USDT_MINT:
            continue

        owner = (
            str(
                post.get("owner")
                or ""
            ).strip()
        )

        if owner != payment_wallet:
            continue

        account_index = post.get(
            "accountIndex"
        )

        post_amount_data = (
            post.get(
                "uiTokenAmount"
            )
            or {}
        )

        post_amount = decimal_value(
            post_amount_data.get(
                "uiAmountString"
            )
        )

        if post_amount is None:
            raw_post = (
                post_amount_data.get(
                    "amount"
                )
            )

            if raw_post is not None:
                post_amount = (
                    Decimal(str(raw_post))
                    / (
                        Decimal(10)
                        ** SOLANA_DECIMALS
                    )
                )

        if post_amount is None:
            continue

        pre_amount = Decimal("0")

        if account_index in pre_by_account:
            pre = pre_by_account[
                account_index
            ]

            pre_amount_data = (
                pre.get(
                    "uiTokenAmount"
                )
                or {}
            )

            pre_amount = decimal_value(
                pre_amount_data.get(
                    "uiAmountString"
                )
            )

            if pre_amount is None:
                raw_pre = (
                    pre_amount_data.get(
                        "amount"
                    )
                )

                if raw_pre is not None:
                    pre_amount = (
                        Decimal(
                            str(raw_pre)
                        )
                        / (
                            Decimal(10)
                            ** SOLANA_DECIMALS
                        )
                    )

        increase = (
            post_amount
            - pre_amount
        )

        if increase > 0:
            matching_amount += increase

            if sender_owner is None:
                sender_owner = (
                    pre.get("owner")
                    if account_index
                    in pre_by_account
                    else None
                )

    if matching_amount <= 0:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "No USDT transfer to the "
                "configured Solana payment "
                "wallet was found."
            ),
        }

    if not within_tolerance(
        matching_amount,
        expected_amount,
        tolerance,
    ):
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "The received USDT amount "
                "does not match the order."
            ),
        }

    reserved = (
        await reserve_transaction_hash(
            transaction_hash,
            order["order_id"],
        )
    )

    if not reserved:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This transaction hash "
                "has already been used."
            ),
        }

    return {
        "valid": True,
        "pending": False,
        "amount": matching_amount,
        "confirmations": "finalized",
        "from": sender_owner,
        "to": payment_wallet,
        "token": SOLANA_USDT_MINT,
    }


# ============================================================
# MAIN VERIFICATION ENTRY POINT
# ============================================================

async def verify_payment(
    order_id: str,
    transaction_hash: str,
) -> Dict[str, Any]:
    transaction_hash = (
        str(transaction_hash or "")
        .strip()
    )

    if not transaction_hash:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Transaction hash is required."
            ),
        }

    order = await get_order_for_payment(
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

    status = str(
        order["status"] or ""
    ).upper()

    if status not in {
        "PAYMENT_SUBMITTED",
        "PENDING_PAYMENT",
    }:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This order is not accepting "
                "payment verification."
            ),
        }

    chain = normalize_chain(
        order["chain"]
    )

    if chain in {
        "bnb",
        "ethereum",
    }:
        return await verify_evm_payment(
            order,
            transaction_hash,
            chain,
        )

    if chain == "solana":
        return await verify_solana_payment(
            order,
            transaction_hash,
        )

    if chain == "robinhood":
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Robinhood Chain payment "
                "verification is not enabled. "
                "Please use BNB, Ethereum, "
                "or Solana payment."
            ),
        }

    return {
        "valid": False,
        "pending": False,
        "reason": (
            "Unsupported payment chain."
        ),
    }
