import asyncio
from decimal import Decimal, InvalidOperation

import httpx

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

ETHEREUM_CHAIN_ID = 1
BNB_CHAIN_ID = 56

EVM_CONFIRMATIONS_REQUIRED = 3

USDT_CONTRACTS = {
    "ethereum": (
        "0xdac17f958d2ee523a2206206994597c13d831ec7"
    ),
    "bnb": (
        "0x55d398326f99059ff775485246999027b3197955"
    ),
}

SOLANA_USDT_MINT = (
    "es9vmfrzacer m jfrf4h2fyd4kco"
    "nk11mcce8benwnyb"
    .replace(" ", "")
    .lower()
)


def normalize_chain(chain):
    chain = (
        str(chain or "")
        .strip()
        .lower()
    )

    aliases = {
        "bsc": "bnb",
        "binance": "bnb",
        "binance-smart-chain": "bnb",
        "eth": "ethereum",
        "mainnet": "ethereum",
        "sol": "solana",
    }

    return aliases.get(
        chain,
        chain,
    )


def decimal_from_raw(
    raw_value,
    decimals,
):
    try:
        return (
            Decimal(str(raw_value))
            / (
                Decimal(10)
                ** int(decimals)
            )
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return Decimal("0")


async def get_system_setting(
    key,
    default=None,
):
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
            return default

        return row["value"]


async def get_payment_tolerance():
    value = await get_system_setting(
        "payment_tolerance_usdt",
        "0.10",
    )

    try:
        tolerance = Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        tolerance = Decimal("0.10")

    if tolerance < 0:
        tolerance = Decimal("0.10")

    return tolerance


async def get_order(order_id):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
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


async def is_tx_hash_used(
    tx_hash,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT id
            FROM used_payment_hashes
            WHERE transaction_hash = $1
            LIMIT 1;
            """,
            tx_hash,
        )

        return row is not None


async def reserve_tx_hash(
    tx_hash,
    order_id,
):
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
                tx_hash,
                order_id,
            )

            return True

        except Exception:
            return False


async def release_tx_hash(
    tx_hash,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            DELETE FROM used_payment_hashes
            WHERE transaction_hash = $1;
            """,
            tx_hash,
        )


async def get_evm_tx_data(
    chain,
    tx_hash,
):
    if not ETHERSCAN_API_KEY:
        return {
            "ok": False,
            "pending": True,
            "reason": (
                "Etherscan API key is not configured."
            ),
        }

    chain = normalize_chain(chain)

    if chain == "ethereum":
        chain_id = ETHEREUM_CHAIN_ID

    elif chain == "bnb":
        chain_id = BNB_CHAIN_ID

    else:
        return {
            "ok": False,
            "pending": False,
            "reason": "Unsupported EVM payment chain.",
        }

    params = {
        "chainid": chain_id,
        "module": "account",
        "action": "tokentx",
        "contractaddress": USDT_CONTRACTS[
            chain
        ],
        "txhash": tx_hash,
        "page": 1,
        "offset": 100,
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

    except Exception as exc:
        return {
            "ok": False,
            "pending": True,
            "reason": (
                "Unable to query Etherscan: "
                f"{exc}"
            ),
        }

    status = str(
        data.get("status", "")
    )

    message = str(
        data.get("message", "")
    )

    result = data.get(
        "result"
    )

    if status == "0":
        result_text = str(
            result or ""
        ).lower()

        if (
            "no transactions found"
            in result_text
            or "not found"
            in result_text
        ):
            return {
                "ok": False,
                "pending": True,
                "reason": (
                    "Transaction was not found "
                    "yet."
                ),
            }

        return {
            "ok": False,
            "pending": True,
            "reason": (
                f"Etherscan response: "
                f"{message or result}"
            ),
        }

    if not isinstance(
        result,
        list,
    ):
        return {
            "ok": False,
            "pending": True,
            "reason": (
                "No token transfer data "
                "is available yet."
            ),
        }

    transfers = []

    for transfer in result:
        transfer_hash = str(
            transfer.get("hash", "")
        ).lower()

        if transfer_hash == tx_hash.lower():
            transfers.append(
                transfer
            )

    if not transfers:
        return {
            "ok": False,
            "pending": True,
            "reason": (
                "No matching USDT transfer "
                "was found yet."
            ),
        }

    return {
        "ok": True,
        "pending": False,
        "transfers": transfers,
        "raw": data,
    }


async def get_evm_receipt(
    chain,
    tx_hash,
):
    rpc_url = None

    if chain == "ethereum":
        rpc_url = (
            "https://ethereum-rpc.publicnode.com"
        )

    elif chain == "bnb":
        rpc_url = (
            "https://bsc-rpc.publicnode.com"
        )

    if not rpc_url:
        return {
            "ok": False,
            "pending": False,
            "reason": "Unsupported EVM chain.",
        }

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [
            tx_hash
        ],
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

    except Exception as exc:
        return {
            "ok": False,
            "pending": True,
            "reason": (
                "Unable to query transaction "
                f"receipt: {exc}"
            ),
        }

    receipt = data.get(
        "result"
    )

    if receipt is None:
        return {
            "ok": False,
            "pending": True,
            "reason": (
                "Transaction has not been "
                "mined yet."
            ),
        }

    status = receipt.get(
        "status"
    )

    if status == "0x0":
        return {
            "ok": False,
            "pending": False,
            "reason": (
                "Transaction failed on-chain."
            ),
        }

    return {
        "ok": True,
        "pending": False,
        "receipt": receipt,
    }


async def get_evm_block_number(
    chain,
):
    if chain == "ethereum":
        rpc_url = (
            "https://ethereum-rpc.publicnode.com"
        )

    elif chain == "bnb":
        rpc_url = (
            "https://bsc-rpc.publicnode.com"
        )

    else:
        return None

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_blockNumber",
        "params": [],
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

            result = data.get(
                "result"
            )

            if not result:
                return None

            return int(
                result,
                16,
            )

    except Exception as exc:
        print(
            "EVM block-number error: "
            f"{exc}"
        )

        return None


async def verify_evm_payment(
    order,
    tx_hash,
):
    chain = normalize_chain(
        order["chain"]
    )

    expected_wallet = str(
        order["payment_wallet"] or ""
    ).lower()

    if not expected_wallet:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Payment wallet is not configured "
                "for this order."
            ),
        }

    expected_amount = Decimal(
        str(order["amount"])
    )

    transfer_result = (
        await get_evm_tx_data(
            chain,
            tx_hash,
        )
    )

    if transfer_result.get(
        "pending"
    ):
        return {
            "valid": False,
            "pending": True,
            "reason": transfer_result.get(
                "reason"
            ),
        }

    if not transfer_result.get(
        "ok"
    ):
        return {
            "valid": False,
            "pending": False,
            "reason": transfer_result.get(
                "reason",
                "Unable to verify payment.",
            ),
        }

    transfers = transfer_result[
        "transfers"
    ]

    expected_contract = (
        USDT_CONTRACTS[chain]
    )

    tolerance = (
        await get_payment_tolerance()
    )

    matching_transfer = None

    for transfer in transfers:
        contract = str(
            transfer.get(
                "contractAddress",
                "",
            )
        ).lower()

        to_address = str(
            transfer.get(
                "to",
                "",
            )
        ).lower()

        if contract != expected_contract:
            continue

        if to_address != expected_wallet:
            continue

        try:
            decimals = int(
                transfer.get(
                    "tokenDecimal",
                    6,
                )
            )

            amount = decimal_from_raw(
                transfer.get(
                    "value",
                    "0",
                ),
                decimals,
            )

        except Exception:
            continue

        difference = abs(
            amount - expected_amount
        )

        if difference <= tolerance:
            matching_transfer = {
                "amount": amount,
                "block_number": int(
                    transfer.get(
                        "blockNumber",
                        "0",
                    )
                ),
                "from": transfer.get(
                    "from"
                ),
                "to": to_address,
                "contract": contract,
            }

            break

    if not matching_transfer:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "No USDT transfer matching "
                "the required amount and "
                "payment wallet was found."
            ),
        }

    receipt_result = (
        await get_evm_receipt(
            chain,
            tx_hash,
        )
    )

    if receipt_result.get(
        "pending"
    ):
        return {
            "valid": False,
            "pending": True,
            "reason": receipt_result.get(
                "reason"
            ),
        }

    if not receipt_result.get(
        "ok"
    ):
        return {
            "valid": False,
            "pending": False,
            "reason": receipt_result.get(
                "reason"
            ),
        }

    receipt = receipt_result[
        "receipt"
    ]

    receipt_block_hex = receipt.get(
        "blockNumber"
    )

    if not receipt_block_hex:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Transaction block is not "
                "available yet."
            ),
        }

    receipt_block = int(
        receipt_block_hex,
        16,
    )

    current_block = (
        await get_evm_block_number(
            chain
        )
    )

    if current_block is None:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Unable to determine "
                "confirmation count."
            ),
        }

    confirmations = (
        current_block
        - receipt_block
        + 1
    )

    if (
        confirmations
        < EVM_CONFIRMATIONS_REQUIRED
    ):
        return {
            "valid": False,
            "pending": True,
            "reason": (
                f"Waiting for confirmations "
                f"({confirmations}/"
                f"{EVM_CONFIRMATIONS_REQUIRED})."
            ),
        }

    return {
        "valid": True,
        "pending": False,
        "amount": matching_transfer[
            "amount"
        ],
        "confirmations": confirmations,
        "block_number": receipt_block,
        "from": matching_transfer[
            "from"
        ],
        "to": matching_transfer[
            "to"
        ],
        "contract": matching_transfer[
            "contract"
        ],
    }


async def helius_request(
    method,
    params,
):
    if not HELIUS_API_KEY:
        return {
            "ok": False,
            "pending": True,
            "reason": (
                "Helius API key is not configured."
            ),
        }

    url = (
        f"{HELIUS_RPC_URL}"
        f"?api-key={HELIUS_API_KEY}"
    )

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    try:
        async with httpx.AsyncClient(
            timeout=30
        ) as client:
            response = await client.post(
                url,
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

    except Exception as exc:
        return {
            "ok": False,
            "pending": True,
            "reason": (
                "Helius request failed: "
                f"{exc}"
            ),
        }

    if data.get("error"):
        return {
            "ok": False,
            "pending": True,
            "reason": str(
                data["error"]
            ),
        }

    return {
        "ok": True,
        "pending": False,
        "result": data.get(
            "result"
        ),
    }


def find_solana_usdt_transfer(
    transaction,
    expected_wallet,
    expected_amount,
):
    meta = transaction.get(
        "meta"
    ) or {}

    if meta.get(
        "err"
    ) is not None:
        return None

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

    pre_map = {}
    post_map = {}

    for item in pre_balances:
        if str(
            item.get("mint", "")
        ).lower() != SOLANA_USDT_MINT:
            continue

        key = (
            item.get(
                "accountIndex"
            ),
            item.get(
                "owner"
            ),
        )

        pre_map[key] = item

    for item in post_balances:
        if str(
            item.get("mint", "")
        ).lower() != SOLANA_USDT_MINT:
            continue

        key = (
            item.get(
                "accountIndex"
            ),
            item.get(
                "owner"
            ),
        )

        post_map[key] = item

    candidates = []

    for key, post in post_map.items():
        owner = str(
            post.get(
                "owner",
                "",
            )
        )

        if owner.lower() != expected_wallet.lower():
            continue

        pre = pre_map.get(
            key
        )

        pre_amount = Decimal("0")
        post_amount = Decimal("0")

        if pre:
            pre_amount = Decimal(
                str(
                    (
                        pre.get(
                            "uiTokenAmount"
                        )
                        or {}
                    ).get(
                        "uiAmountString",
                        "0",
                    )
                )
            )

        post_amount = Decimal(
            str(
                (
                    post.get(
                        "uiTokenAmount"
                    )
                    or {}
                ).get(
                    "uiAmountString",
                    "0",
                )
            )
        )

        increase = (
            post_amount
            - pre_amount
        )

        if increase <= 0:
            continue

        difference = abs(
            increase
            - expected_amount
        )

        if difference <= Decimal(
            "0.10"
        ):
            candidates.append(
                increase
            )

    if not candidates:
        return None

    return max(candidates)


async def verify_solana_payment(
    order,
    tx_hash,
):
    expected_wallet = str(
        order["payment_wallet"] or ""
    )

    if not expected_wallet:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Solana payment wallet is "
                "not configured."
            ),
        }

    expected_amount = Decimal(
        str(order["amount"])
    )

    result = await helius_request(
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

    if result.get(
        "pending"
    ):
        return {
            "valid": False,
            "pending": True,
            "reason": result.get(
                "reason"
            ),
        }

    transaction = result.get(
        "result"
    )

    if transaction is None:
        return {
            "valid": False,
            "pending": True,
            "reason": (
                "Solana transaction was "
                "not found yet."
            ),
        }

    received = find_solana_usdt_transfer(
        transaction,
        expected_wallet,
        expected_amount,
    )

    if received is None:
        meta = transaction.get(
            "meta"
        ) or {}

        if meta.get(
            "err"
        ) is not None:
            return {
                "valid": False,
                "pending": False,
                "reason": (
                    "Solana transaction failed."
                ),
            }

        return {
            "valid": False,
            "pending": False,
            "reason": (
                "No matching USDT transfer "
                "to the configured Solana "
                "payment wallet was found."
            ),
        }

    return {
        "valid": True,
        "pending": False,
        "amount": received,
        "confirmations": "finalized",
    }


async def verify_payment(
    order_id,
    tx_hash,
):
    tx_hash = str(
        tx_hash or ""
    ).strip()

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
            "reason": "Order not found.",
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
                "Order is not accepting "
                "payment verification."
            ),
        }

    stored_hash = str(
        order["transaction_hash"] or ""
    ).strip()

    if (
        stored_hash
        and stored_hash.lower()
        != tx_hash.lower()
    ):
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "This order already has a "
                "different transaction hash."
            ),
        }

    if await is_tx_hash_used(
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

    chain = normalize_chain(
        order["chain"]
    )

    if chain in {
        "ethereum",
        "bnb",
    }:
        result = await verify_evm_payment(
            order,
            tx_hash,
        )

    elif chain == "solana":
        result = await verify_solana_payment(
            order,
            tx_hash,
        )

    else:
        return {
            "valid": False,
            "pending": False,
            "reason": (
                "Payment verification is not "
                "available for this chain."
            ),
        }

    if result.get(
        "valid"
    ):
        reserved = await reserve_tx_hash(
            tx_hash,
            order_id,
        )

        if not reserved:
            return {
                "valid": False,
                "pending": False,
                "reason": (
                    "Transaction hash was already "
                    "reserved by another order."
                ),
            }

    return result
