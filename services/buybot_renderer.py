from decimal import Decimal, InvalidOperation
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from database.connection import get_pool


def money(value) -> str:
    try:
        amount = Decimal(str(value))

        if amount >= Decimal("1000000"):
            return f"${amount / Decimal('1000000'):,.2f}M"

        if amount >= Decimal("1000"):
            return f"${amount / Decimal('1000'):,.2f}K"

        return f"${amount:,.2f}"

    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return "$0.00"


def number(value) -> str:
    try:
        amount = Decimal(str(value))

        if amount == amount.to_integral():
            return f"{int(amount):,}"

        return f"{amount:,.6f}".rstrip("0").rstrip(".")

    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return "0"


def short_address(
    address: Optional[str],
) -> str:
    if not address:
        return ""

    if len(address) <= 12:
        return address

    return (
        f"{address[:6]}..."
        f"{address[-6:]}"
    )


async def get_buybot_settings(
    group_id: int,
):
    pool = get_pool()

    async with pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT
                enabled,
                min_buy_usd,
                show_new_holder,
                show_market_cap,
                show_spent_amount,
                show_received_amount,
                custom_media_type,
                custom_media_file_id,
                buy_emoji,
                spent_emoji,
                received_emoji,
                holder_emoji,
                market_cap_emoji
            FROM buybot_settings
            WHERE group_id = $1
            """,
            group_id,
        )

    if not row:
        return {
            "enabled": True,
            "min_buy_usd": Decimal("0"),
            "show_new_holder": True,
            "show_market_cap": True,
            "show_spent_amount": True,
            "show_received_amount": True,
            "custom_media_type": None,
            "custom_media_file_id": None,
            "buy_emoji": "🟢",
            "spent_emoji": "🔀",
            "received_emoji": "🪙",
            "holder_emoji": "👤",
            "market_cap_emoji": "💎",
        }

    return dict(row)


async def get_buybot_buttons(
    group_id: int,
):
    pool = get_pool()

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT
                button_name,
                button_url,
                position
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC
            """,
            group_id,
        )

    return rows


def build_buybot_keyboard(
    buttons,
    dex_url: Optional[str] = None,
    buy_url: Optional[str] = None,
    trending_url: Optional[str] = None,
):
    keyboard = []

    automatic_buttons = []

    if dex_url:
        automatic_buttons.append(
            InlineKeyboardButton(
                "📊 Chart",
                url=dex_url,
            )
        )

    if buy_url:
        automatic_buttons.append(
            InlineKeyboardButton(
                "🛒 Buy",
                url=buy_url,
            )
        )

    if trending_url:
        automatic_buttons.append(
            InlineKeyboardButton(
                "📈 Trending",
                url=trending_url,
            )
        )

    if automatic_buttons:
        row = []

        for button in automatic_buttons:

            row.append(button)

            if len(row) == 2:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

    custom_row = []

    for button in buttons:

        custom_row.append(
            InlineKeyboardButton(
                button["button_name"],
                url=button["button_url"],
            )
        )

        if len(custom_row) == 2:
            keyboard.append(custom_row)
            custom_row = []

    if custom_row:
        keyboard.append(custom_row)

    if not keyboard:
        return None

    return InlineKeyboardMarkup(
        keyboard
    )


def build_buybot_text(
    *,
    token_name: str,
    token_symbol: str,
    chain: str,
    spent_amount,
    spent_token: str,
    received_amount,
    received_token: str,
    market_cap=None,
    is_new_holder=False,
    settings=None,
):
    if settings is None:
        settings = {
            "buy_emoji": "🟢",
            "spent_emoji": "🔀",
            "received_emoji": "🪙",
            "holder_emoji": "👤",
            "market_cap_emoji": "💎",
            "show_new_holder": True,
            "show_market_cap": True,
            "show_spent_amount": True,
            "show_received_amount": True,
        }

    buy_emoji = (
        settings.get("buy_emoji")
        or "🟢"
    )

    spent_emoji = (
        settings.get("spent_emoji")
        or "🔀"
    )

    received_emoji = (
        settings.get("received_emoji")
        or "🪙"
    )

    holder_emoji = (
        settings.get("holder_emoji")
        or "👤"
    )

    market_cap_emoji = (
        settings.get("market_cap_emoji")
        or "💎"
    )

    lines = []

    lines.append(
        f"🚀 *{token_name}* "
        f"(`{token_symbol}`) Buy!"
    )

    lines.append("")

    lines.append(
        f"{buy_emoji} *BUY DETECTED*"
    )

    lines.append("")

    if settings.get(
        "show_spent_amount",
        True,
    ):
        lines.append(
            f"{spent_emoji} Spent "
            f"*{money(spent_amount)}* "
            f"({number(spent_amount)} "
            f"{spent_token})"
        )

    if settings.get(
        "show_received_amount",
        True,
    ):
        lines.append(
            f"{received_emoji} Got "
            f"*{number(received_amount)} "
            f"{received_token}*"
        )

    lines.append("")

    if (
        is_new_holder
        and settings.get(
            "show_new_holder",
            True,
        )
    ):
        lines.append(
            f"{holder_emoji} *New Holder*"
        )

    if (
        market_cap is not None
        and settings.get(
            "show_market_cap",
            True,
        )
    ):
        lines.append(
            f"{market_cap_emoji} Market Cap "
            f"*{money(market_cap)}*"
        )

    lines.append("")

    lines.append(
        f"⛓️ Network: *{chain}*"
    )

    return "\n".join(lines)


async def render_buybot_alert(
    *,
    group_id: int,
    token_name: str,
    token_symbol: str,
    chain: str,
    spent_amount,
    spent_token: str,
    received_amount,
    received_token: str,
    market_cap=None,
    is_new_holder=False,
    dex_url: Optional[str] = None,
    buy_url: Optional[str] = None,
    trending_url: Optional[str] = None,
):
    settings = await get_buybot_settings(
        group_id
    )

    buttons = await get_buybot_buttons(
        group_id
    )

    text = build_buybot_text(
        token_name=token_name,
        token_symbol=token_symbol,
        chain=chain,
        spent_amount=spent_amount,
        spent_token=spent_token,
        received_amount=received_amount,
        received_token=received_token,
        market_cap=market_cap,
        is_new_holder=is_new_holder,
        settings=settings,
    )

    keyboard = build_buybot_keyboard(
        buttons=buttons,
        dex_url=dex_url,
        buy_url=buy_url,
        trending_url=trending_url,
    )

    return {
        "text": text,
        "reply_markup": keyboard,
        "media_type": settings.get(
            "custom_media_type"
        ),
        "media_file_id": settings.get(
            "custom_media_file_id"
        ),
    }
