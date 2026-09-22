import asyncio
from decimal import Decimal, InvalidOperation

import httpx

from config import ETHERSCAN_API_KEY, HELIUS_API_KEY
from database.connection import get_pool


# ============================================================
# USDT CONTRACTS / MINTS
# ============================================================

USDT_CONTRACTS = {
    "ethereum": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "bnb": "0x55d398326f99059fF775485246999027B3197955",
}

SOLANA_USDT_MINT = (
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
)


# ============================================================
# EVM SETTINGS
# ============================================================

EVM_CHAINS = {
    "ethereum": {
        "chain_id": "1",
    },
    "bnb": {
        "chain_id": "56",
    },
}


ETHERSCAN_V2_URL = (
    "https://api.etherscan.io/v2/api"
)

HELIUS_RPC_URL = (
    "https://mainnet.helius-rpc.com/"
)


# ============================================================
# DEFAULT PAYMENT SETTINGS
# ============================================================

DEFAULT_TOLERANCE = Decimal("0.10")
DEFAULT_CONFIRMATIONS = 3


# ============================================================
# HELPERS
# ============================================================

def decimal_from_string(
    value,
    default=Decimal("0"),
):
    try:
        return Decimal(str(value))
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return default


async def get_payment_setting(
    key: str,
    default: Decimal,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT value
            FROM settings
            WHERE key = $1
            """,
            key,
        )

    if value is None:
        return default

    try:
        return Decimal(str(value))
    except (
        InvalidOperation,
        ValueError,
    ):
        return default


# ============================================================
# GET ORDER
# ============================================================

async def get_order_for_verification(
    order_id: int,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                o.id,
                o.order_number,
                o.chain,
                o.contract_address,
                o.duration_hours,
                o.amount_usdt,
                o.status,
                o.payment_chain,
                o.payment_tx_hash,
                w.address AS payment_wallet
            FROM orders o
            LEFT JOIN wallets w
                ON w.chain = o.payment_chain
            WHERE o.id = $1
            """,
            order_id,
        )

    if not row:
        return None

    return dict(row)


# ============================================================
# ETHERSCAN V2
# ============================================================

async def get_evm_token_transfers(
    chain: str,
    receiver: str,
):
    if not ETHERSCAN_API_KEY:
        return []

    chain_info = EVM_CHAINS.get(chain)

    if not chain_info:
        return []

    token_contract = USDT_CONTRACTS.get(chain)

    if not token_contract:
        return []

    params = {
        "chainid": chain_info["chain_id"],
        "module": "account",
        "action": "tokentx",
        "contractaddress": token_contract,
        "address": receiver,
        "page": "1",
        "offset": "100",
        "sort": "desc",
        "apikey": ETHERSCAN_API_KEY,
    }

    try:
        async with httpx.AsyncClient(
            timeout=20
        ) as client:

            response = await client.get(
                ETHERSCAN_V2_URL,
                params=params,
            )

            response.raise_for_status()

            data = response.json()

    except (
        httpx.HTTPError,
        ValueError,
    ):
        return []

    if data.get("status") != "1":
        return []

    result = data.get("result")

    if not isinstance(result, list):
        return []

    return result


# ============================================================
# VERIFY EVM PAYMENT
# ============================================================

async def verify_evm_payment(
    order: dict,
):
    chain = order["chain"]
    tx_hash = order["payment_tx_hash"]
    receiver = order["payment_wallet"]

    if chain not in EVM_CHAINS:
        return {
            "verified": False,
            "pending": False,
            "reason": "Unsupported EVM chain.",
        }

    if not receiver:
        return {
            "verified": False,
            "pending": False,
            "reason": "Payment wallet is not configured.",
        }

    transfers = await get_evm_token_transfers(
        chain,
        receiver,
    )

    if not transfers:
        return {
            "verified": False,
            "pending": True,
            "reason": (
                "No matching USDT transfers were found yet."
            ),
        }

    expected_receiver = receiver.lower()
    expected_contract = USDT_CONTRACTS[
        chain
    ].lower()

    expected_amount = decimal_from_string(
        order["amount_usdt"]
    )

    tolerance = await get_payment_setting(
        "payment_tolerance_usdt",
        DEFAULT_TOLERANCE,
    )

    required_confirmations = int(
        await get_payment_setting(
            "payment_confirmations",
            Decimal(DEFAULT_CONFIRMATIONS),
        )
    )

    for transfer in transfers:

        transfer_hash = (
            transfer.get("hash")
            or ""
        ).lower()

        if transfer_hash != tx_hash.lower():
            continue

        token_contract = (
            transfer.get("contractAddress")
            or ""
        ).lower()

        if token_contract != expected_contract:
            return {
                "verified": False,
                "pending": False,
                "reason": (
                    "The transaction does not contain "
                    "the correct USDT contract."
                ),
            }

        transfer_to = (
            transfer.get("to")
            or ""
        ).lower()

        if transfer_to != expected_receiver:
            return {
                "verified": False,
                "pending": False,
                "reason": (
                    "USDT was not sent to the configured "
                    "payment wallet."
                ),
            }

        decimals = int(
            transfer.get(
                "tokenDecimal",
                6,
            )
            or 6
        )

        raw_value = decimal_from_string(
            transfer.get("value")
        )

        actual_amount = (
            raw_value
            / (
                Decimal(10)
                ** decimals
            )
        )

        minimum_amount = (
            expected_amount
            - tolerance
        )

        if actual_amount < minimum_amount:
            return {
                "verified": False,
                "pending": False,
                "reason": (
                    f"Payment amount is too low. "
                    f"Expected at least "
                    f"{minimum_amount:g} USDT, "
                    f"received {actual_amount:g} USDT."
                ),
            }

        confirmations = int(
            transfer.get(
                "confirmations",
                0,
            )
            or 0
        )

        if confirmations < required_confirmations:
            return {
                "verified": False,
                "pending": True,
                "reason": (
                    f"Payment found, but it has only "
                    f"{confirmations} confirmations. "
                    f"{required_confirmations} required."
                ),
                "amount": actual_amount,
                "confirmations": confirmations,
            }

        return {
            "verified": True,
            "pending": False,
            "reason": "Payment verified successfully.",
            "amount": actual_amount,
            "confirmations": confirmations,
            "block_number": transfer.get(
                "blockNumber"
            ),
        }

    return {
        "verified": False,
        "pending": True,
        "reason": (
            "The submitted transaction has not "
            "appeared in the USDT transfer history yet."
        ),
    }


# ============================================================
# SOLANA TRANSFER SEARCH
# ============================================================

async def get_solana_transfers(
    receiver: str,
):
    if not HELIUS_API_KEY:
        return []

    payload = {
        "jsonrpc": "2.0",
        "id": "the-block-room-payment",
        "method": "getTransfersByAddress",
        "params": [
            receiver,
            {
                "mint": SOLANA_USDT_MINT,
                "direction": "in",
                "limit": 100,
                "sortOrder": "desc",
            },
        ],
    }

    try:
        async with httpx.AsyncClient(
            timeout=20
        ) as client:

            response = await client.post(
                HELIUS_RPC_URL,
                params={
                    "api-key": HELIUS_API_KEY
                },
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

    except (
        httpx.HTTPError,
        ValueError,
    ):
        return []

    result = data.get("result")

    if not result:
        return []

    transfers = result.get(
        "data",
        []
    )

    if not isinstance(
        transfers,
        list,
    ):
        return []

    return transfers


# ============================================================
# VERIFY SOLANA PAYMENT
# ============================================================

async def verify_solana_payment(
    order: dict,
):
    tx_hash = order["payment_tx_hash"]
    receiver = order["payment_wallet"]

    if not receiver:
        return {
            "verified": False,
            "pending": False,
            "reason": "Payment wallet is not configured.",
        }

    transfers = await get_solana_transfers(
        receiver
    )

    if not transfers:
        return {
            "verified": False,
            "pending": True,
            "reason": (
                "No matching Solana USDT transfers "
                "were found yet."
            ),
        }

    expected_amount = decimal_from_string(
        order["amount_usdt"]
    )

    tolerance = await get_payment_setting(
        "payment_tolerance_usdt",
        DEFAULT_TOLERANCE,
    )

    for transfer in transfers:

        signature = (
            transfer.get("signature")
            or ""
        )

        if signature != tx_hash:
            continue

        mint = (
            transfer.get("mint")
            or ""
        )

        if mint != SOLANA_USDT_MINT:
            return {
                "verified": False,
                "pending": False,
                "reason": (
                    "The transaction does not contain "
                    "the correct Solana USDT mint."
                ),
            }

        to_account = (
            transfer.get("toUserAccount")
            or ""
        )

        if to_account != receiver:
            return {
                "verified": False,
                "pending": False,
                "reason": (
                    "USDT was not sent to the configured "
                    "payment wallet."
                ),
            }

        decimals = int(
            transfer.get(
                "decimals",
                6,
            )
            or 6
        )

        if transfer.get("uiAmount") is not None:
            actual_amount = decimal_from_string(
                transfer.get("uiAmount")
            )
        else:
            raw_amount = decimal_from_string(
                transfer.get("amount")
            )

            actual_amount = (
                raw_amount
                / (
                    Decimal(10)
                    ** decimals
                )
            )

        minimum_amount = (
            expected_amount
            - tolerance
        )

        if actual_amount < minimum_amount:
            return {
                "verified": False,
                "pending": False,
                "reason": (
                    f"Payment amount is too low. "
                    f"Expected at least "
                    f"{minimum_amount:g} USDT, "
                    f"received {actual_amount:g} USDT."
                ),
            }

        confirmation_status = (
            transfer.get(
                "confirmationStatus"
            )
            or ""
        ).lower()

        if confirmation_status != "finalized":
            return {
                "verified": False,
                "pending": True,
                "reason": (
                    "Payment found, but the Solana "
                    "transaction is not finalized yet."
                ),
                "amount": actual_amount,
            }

        return {
            "verified": True,
            "pending": False,
            "reason": "Payment verified successfully.",
            "amount": actual_amount,
            "confirmation_status": confirmation_status,
        }

    return {
        "verified": False,
        "pending": True,
        "reason": (
            "The submitted transaction has not "
            "appeared in the Solana USDT transfer history yet."
        ),
    }


# ============================================================
# MARK PAYMENT VERIFIED
# ============================================================

async def mark_payment_verified(
    order_id: int,
    verification: dict,
):
    pool = get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():

            order = await conn.fetchrow(
                """
                SELECT
                    id,
                    payment_tx_hash,
                    status
                FROM orders
                WHERE id = $1
                FOR UPDATE
                """,
                order_id,
            )

            if not order:
                return False

            if order["status"] == "PAID":
                return True

            tx_hash = order["payment_tx_hash"]

            if not tx_hash:
                return False

            used_hash = await conn.fetchrow(
                """
                SELECT
                    tx_hash,
                    order_id
                FROM used_payment_hashes
                WHERE tx_hash = $1
                FOR UPDATE
                """,
                tx_hash,
            )

            if used_hash:
                return False

            await conn.execute(
                """
                INSERT INTO used_payment_hashes (
                    tx_hash,
                    chain,
                    order_id
                )
                SELECT
                    payment_tx_hash,
                    payment_chain,
                    id
                FROM orders
                WHERE id = $1
                """,
                order_id,
            )

            await conn.execute(
                """
                UPDATE orders
                SET
                    status = 'PAID',
                    payment_verified = TRUE,
                    updated_at = NOW()
                WHERE id = $1
                """,
                order_id,
            )

    return True


# ============================================================
# MAIN VERIFICATION FUNCTION
# ============================================================

async def verify_order_payment(
    order_id: int,
):
    order = await get_order_for_verification(
        order_id
    )

    if not order:
        return {
            "verified": False,
            "pending": False,
            "reason": "Order not found.",
        }

    if order["status"] == "PAID":
        return {
            "verified": True,
            "pending": False,
            "reason": "Payment is already verified.",
        }

    if not order["payment_tx_hash"]:
        return {
            "verified": False,
            "pending": False,
            "reason": "No transaction hash submitted.",
        }

    if order["chain"] in {
        "ethereum",
        "bnb",
    }:
        result = await verify_evm_payment(
            order
        )

    elif order["chain"] == "solana":
        result = await verify_solana_payment(
            order
        )

    else:
        return {
            "verified": False,
            "pending": False,
            "reason": (
                "Payment verification is not "
                "configured for this network."
            ),
        }

    if result.get("verified"):

        marked = await mark_payment_verified(
            order_id,
            result,
        )

        if not marked:
            return {
                "verified": False,
                "pending": False,
                "reason": (
                    "Payment verification succeeded, "
                    "but the order could not be finalized."
                ),
            }

    return result


# ============================================================
# OPTIONAL RETRY HELPER
# ============================================================

async def verify_with_retries(
    order_id: int,
    attempts: int = 3,
    delay_seconds: int = 10,
):
    last_result = None

    for attempt in range(attempts):

        last_result = await verify_order_payment(
            order_id
        )

        if last_result.get("verified"):
            return last_result

        if not last_result.get("pending"):
            return last_result

        if attempt < attempts - 1:
            await asyncio.sleep(
                delay_seconds
            )

    return last_result
