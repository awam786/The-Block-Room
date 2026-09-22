from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class BuyEvent:
    """
    Standardized on-chain BuyBot event.

    Every blockchain detector must convert its
    detected transaction into this structure.
    """

    group_id: int

    chain: str

    tx_hash: str

    token_address: str

    token_name: str

    token_symbol: str

    buyer_address: str

    spent_amount_usd: Decimal

    spent_native_amount: Decimal

    spent_native_symbol: str

    received_amount: Decimal

    received_symbol: str

    market_cap_usd: Decimal

    is_new_holder: bool

    dex_url: Optional[str] = None

    buy_url: Optional[str] = None

    trending_url: Optional[str] = None

    block_number: Optional[int] = None

    timestamp: Optional[int] = None


def normalize_buy_event(
    event: BuyEvent,
) -> BuyEvent:
    """
    Normalize values before rendering/sending.
    """

    event.chain = event.chain.lower().strip()

    event.token_address = (
        event.token_address.strip()
    )

    event.token_name = (
        event.token_name.strip()
    )

    event.token_symbol = (
        event.token_symbol.strip()
    )

    event.buyer_address = (
        event.buyer_address.strip()
    )

    event.spent_native_symbol = (
        event.spent_native_symbol
        .strip()
        .upper()
    )

    event.received_symbol = (
        event.received_symbol
        .strip()
    )

    if event.dex_url:
        event.dex_url = event.dex_url.strip()

    if event.buy_url:
        event.buy_url = event.buy_url.strip()

    if event.trending_url:
        event.trending_url = (
            event.trending_url.strip()
        )

    if event.spent_amount_usd < 0:
        event.spent_amount_usd = Decimal("0")

    if event.spent_native_amount < 0:
        event.spent_native_amount = Decimal("0")

    if event.received_amount < 0:
        event.received_amount = Decimal("0")

    if event.market_cap_usd < 0:
        event.market_cap_usd = Decimal("0")

    return event
