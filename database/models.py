from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class TokenInfo:
    chain: str
    contract_address: str
    name: Optional[str] = None
    symbol: Optional[str] = None


@dataclass
class PriceOption:
    duration_hours: int
    price_usdt: Decimal


@dataclass
class PaymentInfo:
    chain: str
    tx_hash: str
    amount_usdt: Decimal
    receiver: str


@dataclass
class TrendInfo:
    chain: str
    contract_address: str
    token_name: str
    token_symbol: str
    volume_24h: Decimal
    market_cap: Decimal
    liquidity: Decimal
    price_change_24h: Decimal
