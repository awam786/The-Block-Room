import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from config import (
    ETHERSCAN_API_KEY,
    HELIUS_API_KEY,
    ETHEREUM_RPC_URL,
    BNB_RPC_URL,
)

from database.connection import get_pool


# ============================================================
# PAYMENT CONFIGURATION
# ============================================================

USDT_ETHEREUM = (
    "0xdac17f958d2ee523a2206206994597c13d831ec7"
)

USDT_BNB = (
    "0x55d398326f99059ff775485246999027b3197955"
)

# Canonical USDT mint on Solana.
USDT_SOLANA = (
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
)


EVM_USDT_DECIMALS = 6
SOLANA_USDT_DECIMALS = 6

REQUIRED_EVM_CONFIRMATIONS = 3

DEFAULT_TOLERANCE_USDT = Decimal("0.10")

HTTP_TIMEOUT = 20.0


# ============================================================
# GENERAL HELPERS
# ============================================================

def normalize_evm_address(
    address: Optional[str],
) -> str:
    if not address:
        return ""

    return address.strip().lower()


def normalize_tx_hash(
    tx_hash: Optional[str],
) -> str:
    if not tx_hash:
        return ""

    return tx_hash.strip()


def decimal_from_value(
    value: Any,
) -> Optional[Decimal]:
    if value is None:
        return None

    try:
        return Decimal(str(value))
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return None


async def get_payment_tolerance() -> Decimal:
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT value
            FROM system_settings
            WHERE key = 'payment_tolerance_usdt'
            LIMIT 1;
            """
        )

    if not row:
        return DEFAULT_TOLERANCE_USDT

    tolerance = decimal_from_value(
        row["value"]
    )

    if tolerance is None:
        return DEFAULT_TOLERANCE_USDT

    if tolerance < Decimal("0"):
        return DEFAULT_TOLERANCE_USDT

    return tolerance


async def is_transaction_hash_used(
    tx_hash: str,
) -> bool:
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT 1
            FROM used_payment_hashes
            WHERE transaction_hash = $1
            LIMIT 1;
            """,
            tx_hash,
        )

    return row is not None


async def reserve_transaction_hash(
    tx_hash: str,
    order_id: str,
) -> bool:
    """
    Atomically reserve a transaction hash.

    Returns True if the hash was successfully reserved.
    Returns False if it was already reserved.
    """

    pool = await get_pool()

    async with pool.acquire() as connection:
        try:
            row = await connection.fetchrow(
                """
                INSERT INTO used_payment_hashes (
                    transaction_hash,
                    order_id
                )
                VALUES ($1, $2)
                ON CONFLICT (
                    transaction_hash
                )
                DO NOTHING
                RETURNING transaction_hash;
                """,
                tx_hash,
                order_id,
            )
        except Exception as exc:
            print(
                "Payment hash reservation error: "
                f"{exc}"
            )
            return False

    return row is not None


# ============================================================
# ORDER / WALLET LOOKUP
# ============================================================

async def get_order_payment_data(
    order_id: str,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT
                order_id,
                user_id,
                chain,
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

    return row


# ============================================================
# EVM RPC
# ============================================================

async def evm_rpc_call(
    rpc_url: str,
    method: str,
    params: list,
):
    if not rpc_url:
        raise RuntimeError(
            "EVM RPC URL is not configured."
        )

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT
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


async def get_evm_receipt(
    rpc_url: str,
    tx_hash: str,
):
    return await evm_rpc_call(
        rpc_url,
        "eth_getTransactionReceipt",
        [tx_hash],
    )


async def get_evm_block_number(
    rpc_url: str,
) -> int:
    result = await evm_rpc_call(
        rpc_url,
        "eth_blockNumber",
        [],
    )

    if not result:
        raise RuntimeError(
            "Could not retrieve current block."
        )

    return int(result, 16)


# ============================================================
# ETHERSCAN
# ============================================================

async def get_erc20_transfers_by_tx(
    chain_id: int,
    tx_hash: str,
):
    if not ETHERSCAN_API_KEY:
        raise RuntimeError(
            "ETHERSCAN_API_KEY is not configured."
        )

    params = {
        "chainid": str(chain_id),
        "module": "account",
        "action": "tokentx",
        "txhash": tx_hash,
        "apikey": ETHERSCAN_API_KEY,
    }

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT
    ) as client:
        response = await client.get(
            "https://api.etherscan.io/v2/api",
            params=params,
        )

        response.raise_for_status()

        data = response.json()

    result = data.get("result")

    if not isinstance(result, list):
        return []

    return result


# ============================================================
# EVM PAYMENT VERIFICATION
# ============================================================

async def verify_evm_payment(
    order_id: str,
    tx_hash: str,
    order,
):
    chain = (
        str(order["chain"])
        .strip()
        .lower()
    )

    expected_wallet = normalize_evm_address(
        str(order["payment_wallet"])
    )

    expected_amount = decimal_from_value(
        order["amount"]
    )

    if expected_amount is None:
        return {
            "valid": False,
            "pending": False,
            "reason": "Invalid order amount.",
        }

    if chain == "ethereum":
        chain_id = 1
        token_contract = USDT_ETHEREUM
        rpc_url = ETHEREUM_RPC_URL

    elif chain == "bnb":
        chain_id = 56
        token_contract = USDT_BNB
        rpc_url = BNB_RPC_URL

    else:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Unsupported EVM payment chain."
            ),
        }

    # --------------------------------------------------------
    # Check transaction receipt
    # --------------------------------------------------------

    try:
        receipt = await get_evm_receipt(
            rpc_url,
            tx_hash,
        )
    except Exception as exc:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Unable to retrieve transaction "
                f"receipt: {exc}"
            ),
        }

    if not receipt:
        return {
            "valid": False,
            "pending": True,
            "reason": "Transaction is not mined yet.",
        }

    receipt_status = receipt.get(
        "status"
    )

    if receipt_status not in (
        "0x1",
        "0x01",
    ):
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Transaction failed on the network."
            ),
        }

    block_number_hex = receipt.get(
        "blockNumber"
    )

    if not block_number_hex:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Transaction block is not available yet."
            ),
        }

    transaction_block = int(
        block_number_hex,
        16,
    )

    try:
        current_block = await get_evm_block_number(
            rpc_url
        )
    except Exception as exc:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Unable to determine "
                f"confirmations: {exc}"
            ),
        }

    confirmations = (
        current_block
        - transaction_block
        + 1
    )

    if confirmations < REQUIRED_EVM_CONFIRMATIONS:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                f"Waiting for confirmations "
                f"({confirmations}/"
                f"{REQUIRED_EVM_CONFIRMATIONS})."
            ),
        }

    # --------------------------------------------------------
    # Read USDT transfers
    # --------------------------------------------------------

    try:
        transfers = (
            await get_erc20_transfers_by_tx(
                chain_id,
                tx_hash,
            )
        )
    except Exception as exc:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Unable to retrieve token "
                f"transfer data: {exc}"
            ),
        }

    if not transfers:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "No ERC-20 token transfer was "
                "found for this transaction."
            ),
        }

    tolerance = (
        await get_payment_tolerance()
    )

    matching_transfer = None

    for transfer in transfers:
        contract = normalize_evm_address(
            transfer.get("contractAddress")
        )

        if contract != token_contract:
            continue

        recipient = normalize_evm_address(
            transfer.get("to")
        )

        if recipient != expected_wallet:
            continue

        raw_value = transfer.get(
            "value"
        )

        try:
            token_amount = (
                Decimal(str(raw_value))
                / Decimal(10 ** EVM_USDT_DECIMALS)
            )
        except (
            InvalidOperation,
            ValueError,
            TypeError,
        ):
            continue

        difference = abs(
            token_amount - expected_amount
        )

        if difference > tolerance:
            continue

        matching_transfer = {
            "amount": token_amount,
            "contract": contract,
            "recipient": recipient,
            "from": normalize_evm_address(
                transfer.get("from")
            ),
        }

        break

    if matching_transfer is None:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "No matching USDT transfer was "
                "found for the required wallet "
                "and amount."
            ),
        }

    # --------------------------------------------------------
    # Reserve tx hash only after every payment
    # condition has passed.
    # --------------------------------------------------------

    reserved = await reserve_transaction_hash(
        tx_hash,
        order_id,
    )

    if not reserved:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This transaction hash has "
                "already been used."
            ),
        }

    return {
        "valid": True,
        "pending": False,
        "amount": matching_transfer[
            "amount"
        ],
        "confirmations": confirmations,
        "token_contract": token_contract,
        "recipient": expected_wallet,
    }


# ============================================================
# SOLANA JSON-RPC
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
        timeout=HTTP_TIMEOUT
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


# ============================================================
# SOLANA PAYMENT VERIFICATION
# ============================================================

async def verify_solana_payment(
    order_id: str,
    tx_hash: str,
    order,
):
    expected_wallet = str(
        order["payment_wallet"]
    ).strip()

    expected_amount = decimal_from_value(
        order["amount"]
    )

    if expected_amount is None:
        return {
            "valid": False,
            "pending": False,
            "reason": "Invalid order amount.",
        }

    # --------------------------------------------------------
    # Retrieve finalized transaction
    # --------------------------------------------------------

    try:
        result = await solana_rpc_call(
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
    except Exception as exc:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Unable to retrieve Solana "
                f"transaction: {exc}"
            ),
        }

    if result is None:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Solana transaction is not "
                "finalized yet."
            ),
        }

    meta = result.get("meta") or {}

    if meta.get("err") is not None:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Solana transaction failed."
            ),
        }

    # --------------------------------------------------------
    # Token balance changes
    # --------------------------------------------------------

    pre_balances = (
        meta.get("preTokenBalances")
        or []
    )

    post_balances = (
        meta.get("postTokenBalances")
        or []
    )

    pre_by_key = {}
    post_by_key = {}

    for balance in pre_balances:
        mint = str(
            balance.get("mint") or ""
        ).strip()

        owner = str(
            balance.get("owner") or ""
        ).strip()

        account_index = balance.get(
            "accountIndex"
        )

        key = (
            mint,
            owner,
            account_index,
        )

        pre_by_key[key] = balance

    for balance in post_balances:
        mint = str(
            balance.get("mint") or ""
        ).strip()

        owner = str(
            balance.get("owner") or ""
        ).strip()

        account_index = balance.get(
            "accountIndex"
        )

        key = (
            mint,
            owner,
            account_index,
        )

        post_by_key[key] = balance

    matching_amount = Decimal("0")

    for key, post in post_by_key.items():
        mint, owner, _ = key

        if mint != USDT_SOLANA:
            continue

        if owner != expected_wallet:
            continue

        post_token_amount = (
            post.get("uiTokenAmount")
            or {}
        )

        post_amount = decimal_from_value(
            post_token_amount.get(
                "uiAmountString"
            )
        )

        if post_amount is None:
            raw_post = (
                post_token_amount.get(
                    "amount"
                )
            )

            try:
                post_amount = (
                    Decimal(str(raw_post))
                    / Decimal(
                        10 ** SOLANA_USDT_DECIMALS
                    )
                )
            except (
                InvalidOperation,
                ValueError,
                TypeError,
            ):
                continue

        pre = pre_by_key.get(key)

        pre_amount = Decimal("0")

        if pre:
            pre_token_amount = (
                pre.get("uiTokenAmount")
                or {}
            )

            pre_amount = decimal_from_value(
                pre_token_amount.get(
                    "uiAmountString"
                )
            )

            if pre_amount is None:
                raw_pre = (
                    pre_token_amount.get(
                        "amount"
                    )
                )

                try:
                    pre_amount = (
                        Decimal(str(raw_pre))
                        / Decimal(
                            10 ** SOLANA_USDT_DECIMALS
                        )
                    )
                except (
                    InvalidOperation,
                    ValueError,
                    TypeError,
                ):
                    pre_amount = Decimal("0")

        increase = (
            post_amount - pre_amount
        )

        if increase > matching_amount:
            matching_amount = increase

    if matching_amount <= Decimal("0"):
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "No USDT balance increase was "
                "found for the payment wallet."
            ),
        }

    tolerance = (
        await get_payment_tolerance()
    )

    difference = abs(
        matching_amount - expected_amount
    )

    if difference > tolerance:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "The received USDT amount does "
                "not match the order amount."
            ),
        }

    # --------------------------------------------------------
    # Reserve hash after all checks pass.
    # --------------------------------------------------------

    reserved = await reserve_transaction_hash(
        tx_hash,
        order_id,
    )

    if not reserved:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This transaction hash has "
                "already been used."
            ),
        }

    return {
        "valid": True,
        "pending": False,
        "amount": matching_amount,
        "token_contract": USDT_SOLANA,
        "recipient": expected_wallet,
        "finalized": True,
    }


# ============================================================
# PUBLIC VERIFICATION FUNCTION
# ============================================================

async def verify_payment(
    order_id: str,
    tx_hash: str,
):
    tx_hash = normalize_tx_hash(
        tx_hash
    )

    if not order_id:
        return {
            "valid": False,
            "pending": False,
            "reason": "Missing order ID.",
        }

    if not tx_hash:
        return {
            "valid": False,
            "pending": False,
            "reason": "Missing transaction hash.",
        }

    order = await get_order_payment_data(
        order_id
    )

    if not order:
        return {
            "valid": False,
            "pending": False,
            "reason": "Order not found.",
        }

    status = str(
        order["status"] or ""
    ).upper()

    if status not in (
        "PAYMENT_SUBMITTED",
        "PENDING_PAYMENT",
    ):
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This order is not accepting "
                "payment verification."
            ),
        }

    # Do not allow a transaction hash that is
    # already attached to a different used payment.
    if await is_transaction_hash_used(
        tx_hash
    ):
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This transaction hash has "
                "already been used."
            ),
        }

    chain = str(
        order["chain"] or ""
    ).strip().lower()

    if chain == "solana":
        return await verify_solana_payment(
            order_id,
            tx_hash,
            order,
        )

    if chain in (
        "bnb",
        "ethereum",
    ):
        return await verify_evm_payment(
            order_id,
            tx_hash,
            order,
        )

    return {
        "valid": False,
        "pending": False,
        "reason": (
            f"Unsupported payment chain: "
            f"{chain}"
        ),
    }
