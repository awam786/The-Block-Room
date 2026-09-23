from __future__ import annotations

from typing import Any, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
)

from database.connection import get_pool


# =========================================================
# DEFAULT BUYBOT STYLE
# =========================================================

DEFAULT_ALERT_TITLE = "⚡ NEW BUY DETECTED"

DEFAULT_BUY_EMOJI = "🟢"
DEFAULT_NEW_HOLDER_EMOJI = "👤"
DEFAULT_MARKET_CAP_EMOJI = "💎"
DEFAULT_SPENT_EMOJI = "💰"
DEFAULT_RECEIVED_EMOJI = "📦"
DEFAULT_NETWORK_EMOJI = "⛓️"

DEFAULT_ALERT_TEMPLATE = (
    "{title}\n\n"
    "{buy_emoji} {token_name} ({token_symbol})\n\n"
    "{spent_emoji} Spent: {spent}\n"
    "{received_emoji} Received: {received}\n"
    "{new_holder_emoji} Buyer: {buyer_short}\n"
    "{market_cap_emoji} Market Cap: {market_cap}\n"
    "{network_emoji} Network: {network}\n\n"
    "🔎 Tx: {tx_short}"
)


# =========================================================
# DATABASE
# =========================================================


async def get_buybot_settings(
    group_id: int,
) -> Optional[dict[str, Any]]:
    pool = await get_pool()

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT
                group_id,
                enabled,
                min_buy_usd,
                media_type,
                media_id,
                alert_title,
                alert_template,
                buy_emoji,
                new_holder_emoji,
                market_cap_emoji,
                spent_emoji,
                received_emoji,
                network_emoji
            FROM buybot_settings
            WHERE group_id = $1
            """,
            group_id,
        )

    if not row:
        return None

    return dict(row)


async def get_custom_buttons(
    group_id: int,
) -> list[dict[str, Any]]:
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                id,
                button_name,
                button_url,
                position
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC
            """,
            group_id,
        )

    return [dict(row) for row in rows]


# =========================================================
# FORMATTING HELPERS
# =========================================================


def short_address(
    address: Optional[str],
    left: int = 6,
    right: int = 4,
) -> str:
    if not address:
        return "Unknown"

    address = str(address)

    if len(address) <= left + right + 3:
        return address

    return (
        f"{address[:left]}"
        f"..."
        f"{address[-right:]}"
    )


def format_usd(
    value: Any,
) -> str:
    if value is None:
        return "$0"

    try:
        number = float(value)
    except (
        TypeError,
        ValueError,
    ):
        return str(value)

    if number >= 1_000_000_000:
        return f"${number / 1_000_000_000:.2f}B"

    if number >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"

    if number >= 1_000:
        return f"${number / 1_000:.2f}K"

    if number >= 1:
        return f"${number:,.2f}"

    if number > 0:
        return f"${number:.4f}"

    return "$0"


def format_token_amount(
    value: Any,
) -> str:
    if value is None:
        return "0"

    try:
        number = float(value)
    except (
        TypeError,
        ValueError,
    ):
        return str(value)

    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"

    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"

    if number >= 1_000:
        return f"{number / 1_000:.2f}K"

    if number >= 1:
        return f"{number:,.4f}".rstrip("0").rstrip(".")

    if number > 0:
        return f"{number:.8f}".rstrip("0").rstrip(".")

    return "0"


def normalize_chain(
    chain: Optional[str],
) -> str:
    if not chain:
        return "Unknown"

    value = str(chain).strip().lower()

    mapping = {
        "bnb": "BNB Smart Chain",
        "bsc": "BNB Smart Chain",
        "binance": "BNB Smart Chain",
        "ethereum": "Ethereum",
        "eth": "Ethereum",
        "solana": "Solana",
        "sol": "Solana",
        "robinhood": "Robinhood Chain",
        "robinhood chain": "Robinhood Chain",
    }

    return mapping.get(
        value,
        str(chain),
    )


def safe_template_replace(
    template: str,
    values: dict[str, Any],
) -> str:
    """
    Replace known placeholders without allowing
    a malformed custom template to crash the BuyBot.
    """

    result = template

    for key, value in values.items():
        result = result.replace(
            "{" + key + "}",
            str(value),
        )

    return result


# =========================================================
# AUTOMATIC BUTTONS
# =========================================================


def build_automatic_buttons(
    *,
    dex_url: Optional[str],
    token_address: Optional[str],
    chain: Optional[str],
    trending_url: Optional[str] = None,
) -> list[list[InlineKeyboardButton]]:
    buttons: list[list[InlineKeyboardButton]] = []

    row: list[InlineKeyboardButton] = []

    if dex_url:
        row.append(
            InlineKeyboardButton(
                "📊 Chart",
                url=dex_url,
            )
        )

    if row:
        buttons.append(row)

    row = []

    if token_address:
        explorer_url = build_explorer_url(
            chain=chain,
            address=token_address,
        )

        if explorer_url:
            row.append(
                InlineKeyboardButton(
                    "🔗 Token",
                    url=explorer_url,
                )
            )

    if trending_url:
        row.append(
            InlineKeyboardButton(
                "📈 Trending",
                url=trending_url,
            )
        )

    if row:
        buttons.append(row)

    return buttons


def build_explorer_url(
    *,
    chain: Optional[str],
    address: Optional[str],
) -> Optional[str]:
    if not address:
        return None

    normalized = (
        str(chain or "")
        .strip()
        .lower()
    )

    if normalized in {
        "ethereum",
        "eth",
    }:
        return (
            "https://etherscan.io/token/"
            f"{address}"
        )

    if normalized in {
        "bnb",
        "bsc",
        "binance",
    }:
        return (
            "https://bscscan.com/token/"
            f"{address}"
        )

    if normalized in {
        "solana",
        "sol",
    }:
        return (
            "https://solscan.io/token/"
            f"{address}"
        )

    if normalized in {
        "robinhood",
        "robinhood chain",
    }:
        return (
            "https://explorer.mainnet"
            ".chain.robinhood.com/address/"
            f"{address}"
        )

    return None


def append_custom_buttons(
    keyboard: list[list[InlineKeyboardButton]],
    custom_buttons: list[dict[str, Any]],
) -> None:
    """
    Adds administrator-created buttons.

    The database limits these to three through
    the BuyBot settings flow, but this function
    also protects the renderer from unexpected data.
    """

    valid_buttons = []

    for button in custom_buttons[:3]:
        name = str(
            button.get(
                "button_name",
                "",
            )
        ).strip()

        url = str(
            button.get(
                "button_url",
                "",
            )
        ).strip()

        if not name or not url:
            continue

        valid_buttons.append(
            InlineKeyboardButton(
                name[:64],
                url=url,
            )
        )

    if not valid_buttons:
        return

    # Two buttons per row keeps the alert compact.
    for index in range(
        0,
        len(valid_buttons),
        2,
    ):
        keyboard.append(
            valid_buttons[
                index:index + 2
            ]
        )


# =========================================================
# ALERT TEXT
# =========================================================


def build_alert_text(
    *,
    settings: Optional[dict[str, Any]],
    token_name: Optional[str],
    token_symbol: Optional[str],
    chain: Optional[str],
    buyer_address: Optional[str],
    spent_amount_usd: Any,
    received_amount: Any,
    market_cap_usd: Any,
    tx_hash: Optional[str],
) -> str:
    settings = settings or {}

    title = (
        settings.get("alert_title")
        or DEFAULT_ALERT_TITLE
    )

    template = (
        settings.get("alert_template")
        or DEFAULT_ALERT_TEMPLATE
    )

    buy_emoji = (
        settings.get("buy_emoji")
        or DEFAULT_BUY_EMOJI
    )

    new_holder_emoji = (
        settings.get("new_holder_emoji")
        or DEFAULT_NEW_HOLDER_EMOJI
    )

    market_cap_emoji = (
        settings.get("market_cap_emoji")
        or DEFAULT_MARKET_CAP_EMOJI
    )

    spent_emoji = (
        settings.get("spent_emoji")
        or DEFAULT_SPENT_EMOJI
    )

    received_emoji = (
        settings.get("received_emoji")
        or DEFAULT_RECEIVED_EMOJI
    )

    network_emoji = (
        settings.get("network_emoji")
        or DEFAULT_NETWORK_EMOJI
    )

    display_name = (
        token_name
        or token_symbol
        or "Unknown Token"
    )

    symbol = (
        token_symbol
        or "TOKEN"
    )

    normalized_chain = normalize_chain(
        chain
    )

    buyer_short = short_address(
        buyer_address
    )

    tx_short = short_address(
        tx_hash,
        left=8,
        right=6,
    )

    values = {
        "title": title,
        "token_name": display_name,
        "token_symbol": symbol,
        "chain": normalized_chain,
        "network": normalized_chain,
        "buyer": buyer_short,
        "buyer_short": buyer_short,
        "spent": format_usd(
            spent_amount_usd
        ),
        "spent_usd": format_usd(
            spent_amount_usd
        ),
        "received": format_token_amount(
            received_amount
        ),
        "received_amount": format_token_amount(
            received_amount
        ),
        "market_cap": format_usd(
            market_cap_usd
        ),
        "market_cap_usd": format_usd(
            market_cap_usd
        ),
        "tx_hash": tx_hash or "Unknown",
        "tx_short": tx_short,
        "buy_emoji": buy_emoji,
        "new_holder_emoji": new_holder_emoji,
        "market_cap_emoji": market_cap_emoji,
        "spent_emoji": spent_emoji,
        "received_emoji": received_emoji,
        "network_emoji": network_emoji,
    }

    text = safe_template_replace(
        template,
        values,
    )

    # Protect against an empty custom template.
    if not text.strip():
        text = safe_template_replace(
            DEFAULT_ALERT_TEMPLATE,
            values,
        )

    return text.strip()


# =========================================================
# MEDIA SENDING
# =========================================================


async def send_buybot_media(
    *,
    bot,
    chat_id: int,
    media_type: str,
    media_id: str,
    caption: str,
    reply_markup: Optional[
        InlineKeyboardMarkup
    ],
):
    media_type = (
        media_type or ""
    ).strip().lower()

    if media_type == "photo":
        return await bot.send_photo(
            chat_id=chat_id,
            photo=media_id,
            caption=caption,
            reply_markup=reply_markup,
        )

    if media_type == "video":
        return await bot.send_video(
            chat_id=chat_id,
            video=media_id,
            caption=caption,
            reply_markup=reply_markup,
        )

    if media_type == "animation":
        return await bot.send_animation(
            chat_id=chat_id,
            animation=media_id,
            caption=caption,
            reply_markup=reply_markup,
        )

    return await bot.send_message(
        chat_id=chat_id,
        text=caption,
        reply_markup=reply_markup,
    )


# =========================================================
# MAIN ALERT SENDER
# =========================================================


async def send_buy_alert(
    *,
    bot,
    group_id: int,
    chain: str,
    token_address: str,
    token_name: Optional[str],
    token_symbol: Optional[str],
    buyer_address: Optional[str],
    spent_amount_usd: Any,
    received_amount: Any,
    tx_hash: str,
    market_cap_usd: Any = None,
    dex_url: Optional[str] = None,
    trending_url: Optional[str] = None,
):
    """
    Shared BuyBot alert sender.

    Both EVM and Solana detectors should call
    this function after successfully detecting
    and storing a BUY event.
    """

    settings = await get_buybot_settings(
        group_id
    )

    if not settings:
        return None

    if not settings.get("enabled"):
        return None

    # -----------------------------------------------------
    # Minimum BUY filter
    # -----------------------------------------------------

    minimum_buy = settings.get(
        "min_buy_usd"
    )

    try:
        minimum_buy_value = float(
            minimum_buy or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        minimum_buy_value = 0

    try:
        actual_buy_value = float(
            spent_amount_usd or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        actual_buy_value = 0

    if (
        minimum_buy_value > 0
        and actual_buy_value < minimum_buy_value
    ):
        return None

    # -----------------------------------------------------
    # Build text
    # -----------------------------------------------------

    text = build_alert_text(
        settings=settings,
        token_name=token_name,
        token_symbol=token_symbol,
        chain=chain,
        buyer_address=buyer_address,
        spent_amount_usd=spent_amount_usd,
        received_amount=received_amount,
        market_cap_usd=market_cap_usd,
        tx_hash=tx_hash,
    )

    # -----------------------------------------------------
    # Build buttons
    # -----------------------------------------------------

    keyboard = build_automatic_buttons(
        dex_url=dex_url,
        token_address=token_address,
        chain=chain,
        trending_url=trending_url,
    )

    custom_buttons = (
        await get_custom_buttons(
            group_id
        )
    )

    append_custom_buttons(
        keyboard,
        custom_buttons,
    )

    reply_markup = (
        InlineKeyboardMarkup(keyboard)
        if keyboard
        else None
    )

    # -----------------------------------------------------
    # Send configured media or text
    # -----------------------------------------------------

    media_type = settings.get(
        "media_type"
    )

    media_id = settings.get(
        "media_id"
    )

    if media_type and media_id:
        return await send_buybot_media(
            bot=bot,
            chat_id=group_id,
            media_type=media_type,
            media_id=media_id,
            caption=text,
            reply_markup=reply_markup,
        )

    return await bot.send_message(
        chat_id=group_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


# =========================================================
# COMPATIBILITY ALIASES
# =========================================================


async def send_buybot_alert(
    **kwargs,
):
    """
    Compatibility wrapper.

    Detectors can use either:
        send_buy_alert(...)
    or:
        send_buybot_alert(...)
    """

    return await send_buy_alert(
        **kwargs
    )


async def render_buy_alert(
    **kwargs,
) -> str:
    """
    Render-only helper for testing or previews.
    Does not send anything to Telegram.
    """

    settings = kwargs.pop(
        "settings",
        None,
    )

    return build_alert_text(
        settings=settings,
        token_name=kwargs.get(
            "token_name"
        ),
        token_symbol=kwargs.get(
            "token_symbol"
        ),
        chain=kwargs.get(
            "chain"
        ),
        buyer_address=kwargs.get(
            "buyer_address"
        ),
        spent_amount_usd=kwargs.get(
            "spent_amount_usd"
        ),
        received_amount=kwargs.get(
            "received_amount"
        ),
        market_cap_usd=kwargs.get(
            "market_cap_usd"
        ),
        tx_hash=kwargs.get(
            "tx_hash"
        ),
    )
