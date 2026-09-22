from decimal import Decimal, InvalidOperation
from typing import Any

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)


# ============================================================
# CHAIN CONFIGURATION
# ============================================================

CHAIN_CONFIG = {
    "solana": {
        "name": "Solana",
        "explorer": "https://solscan.io/tx/",
        "address_explorer": "https://solscan.io/account/",
        "default_emoji": "🟣",
    },
    "bnb": {
        "name": "BNB Smart Chain",
        "explorer": "https://bscscan.com/tx/",
        "address_explorer": "https://bscscan.com/address/",
        "default_emoji": "🟡",
    },
    "ethereum": {
        "name": "Ethereum",
        "explorer": "https://etherscan.io/tx/",
        "address_explorer": "https://etherscan.io/address/",
        "default_emoji": "🔵",
    },
    "robinhood": {
        "name": "Robinhood Chain",
        "explorer": "https://explorer.mainnet.chain.robinhood.com/tx/",
        "address_explorer": "https://explorer.mainnet.chain.robinhood.com/address/",
        "default_emoji": "🔴",
    },
}


# ============================================================
# DEFAULT EMOJIS
# ============================================================

DEFAULT_EMOJIS = {
    "buy": "🟢",
    "spent": "💰",
    "received": "🪙",
    "holder": "👤",
    "market_cap": "📊",
    "network": "🌐",
    "chart": "📈",
    "wallet": "👛",
    "transaction": "🔗",
}


# ============================================================
# SAFE VALUE HELPERS
# ============================================================

def safe_text(
    value: Any,
    fallback: str = "",
) -> str:
    if value is None:
        return fallback

    text = str(value).strip()

    if not text:
        return fallback

    return text


def safe_decimal(
    value: Any,
):
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


def format_number(
    value: Any,
    decimals: int = 2,
) -> str:
    number = safe_decimal(value)

    if number is None:
        return "N/A"

    if number >= Decimal("1000000000"):
        return (
            f"{number / Decimal('1000000000'):.2f}B"
        )

    if number >= Decimal("1000000"):
        return (
            f"{number / Decimal('1000000'):.2f}M"
        )

    if number >= Decimal("1000"):
        return (
            f"{number / Decimal('1000'):.2f}K"
        )

    return f"{number:,.{decimals}f}"


def format_usd(
    value: Any,
) -> str:
    number = safe_decimal(value)

    if number is None:
        return "N/A"

    if number >= Decimal("1000000000"):
        return (
            f"${number / Decimal('1000000000'):.2f}B"
        )

    if number >= Decimal("1000000"):
        return (
            f"${number / Decimal('1000000'):.2f}M"
        )

    if number >= Decimal("1000"):
        return (
            f"${number / Decimal('1000'):.2f}K"
        )

    if number < Decimal("0.01"):
        return f"${number:.6f}"

    return f"${number:,.2f}"


def format_token_amount(
    value: Any,
    symbol: str,
) -> str:
    number = safe_decimal(value)

    if number is None:
        return f"N/A {symbol}"

    if number >= Decimal("1000000"):
        amount = (
            f"{number / Decimal('1000000'):.2f}M"
        )
    elif number >= Decimal("1000"):
        amount = (
            f"{number / Decimal('1000'):.2f}K"
        )
    elif number >= Decimal("1"):
        amount = f"{number:,.2f}"
    elif number >= Decimal("0.01"):
        amount = f"{number:.4f}"
    else:
        amount = f"{number:.8f}"

    return f"{amount} {symbol}"


def short_address(
    address: Any,
    left: int = 6,
    right: int = 5,
) -> str:
    address = safe_text(
        address,
        "Unknown",
    )

    if len(address) <= (
        left + right + 3
    ):
        return address

    return (
        f"{address[:left]}"
        f"..."
        f"{address[-right:]}"
    )


# ============================================================
# CHAIN HELPERS
# ============================================================

def normalize_chain(
    chain: Any,
) -> str:
    value = safe_text(
        chain
    ).lower()

    aliases = {
        "sol": "solana",
        "solana": "solana",
        "bnb": "bnb",
        "bsc": "bnb",
        "binance": "bnb",
        "ethereum": "ethereum",
        "eth": "ethereum",
        "robinhood": "robinhood",
        "robinhood chain": "robinhood",
    }

    return aliases.get(
        value,
        value,
    )


def chain_name(
    chain: Any,
) -> str:
    normalized = normalize_chain(
        chain
    )

    config = CHAIN_CONFIG.get(
        normalized
    )

    if config:
        return config["name"]

    return safe_text(
        chain,
        "Unknown",
    )


def chain_emoji(
    chain: Any,
    custom_emoji: Any = None,
) -> str:
    custom = safe_text(
        custom_emoji
    )

    if custom:
        return custom

    normalized = normalize_chain(
        chain
    )

    config = CHAIN_CONFIG.get(
        normalized
    )

    if config:
        return config[
            "default_emoji"
        ]

    return "🌐"


# ============================================================
# URL BUILDERS
# ============================================================

def transaction_url(
    chain: Any,
    tx_hash: Any,
) -> str | None:
    tx_hash = safe_text(
        tx_hash
    )

    if not tx_hash:
        return None

    normalized = normalize_chain(
        chain
    )

    config = CHAIN_CONFIG.get(
        normalized
    )

    if not config:
        return None

    return (
        config["explorer"]
        + tx_hash
    )


def address_url(
    chain: Any,
    address: Any,
) -> str | None:
    address = safe_text(
        address
    )

    if not address:
        return None

    normalized = normalize_chain(
        chain
    )

    config = CHAIN_CONFIG.get(
        normalized
    )

    if not config:
        return None

    return (
        config["address_explorer"]
        + address
    )


# ============================================================
# DEFAULT BUTTONS
# ============================================================

def build_automatic_buttons(
    chain: Any,
    tx_hash: Any = None,
    token_address: Any = None,
    dex_url: Any = None,
    trending_url: Any = None,
):
    buttons = []

    tx_url = transaction_url(
        chain,
        tx_hash,
    )

    if tx_url:
        buttons.append(
            InlineKeyboardButton(
                "🔎 TX",
                url=tx_url,
            )
        )

    dex_url = safe_text(
        dex_url
    )

    if dex_url:
        buttons.append(
            InlineKeyboardButton(
                "📈 Chart",
                url=dex_url,
            )
        )

    token_address = safe_text(
        token_address
    )

    if token_address:
        normalized = normalize_chain(
            chain
        )

        if normalized in CHAIN_CONFIG:
            buttons.append(
                InlineKeyboardButton(
                    "🪙 Token",
                    url=address_url(
                        normalized,
                        token_address,
                    ),
                )
            )

    trending_url = safe_text(
        trending_url
    )

    if trending_url:
        buttons.append(
            InlineKeyboardButton(
                "🔥 Trending",
                url=trending_url,
            )
        )

    return buttons


# ============================================================
# CUSTOM BUTTONS
# ============================================================

def build_custom_buttons(
    button_rows,
):
    buttons = []

    if not button_rows:
        return buttons

    for row in button_rows:
        try:
            name = safe_text(
                row["button_name"]
            )

            url = safe_text(
                row["button_url"]
            )

        except (
            KeyError,
            TypeError,
        ):
            continue

        if not name or not url:
            continue

        buttons.append(
            InlineKeyboardButton(
                name,
                url=url,
            )
        )

    return buttons


def arrange_buttons(
    automatic_buttons,
    custom_buttons,
):
    """
    Creates a clean two-column keyboard.

    Automatic buttons are shown first.
    Custom group buttons follow them.
    """

    all_buttons = []

    for button in (
        automatic_buttons
        or []
    ):
        if button:
            all_buttons.append(
                button
            )

    for button in (
        custom_buttons
        or []
    ):
        if button:
            all_buttons.append(
                button
            )

    keyboard = []

    current_row = []

    for button in all_buttons:
        current_row.append(
            button
        )

        if len(current_row) == 2:
            keyboard.append(
                current_row
            )
            current_row = []

    if current_row:
        keyboard.append(
            current_row
        )

    if not keyboard:
        return None

    return InlineKeyboardMarkup(
        keyboard
    )


# ============================================================
# EMOJI SETTINGS
# ============================================================

def get_emoji(
    settings,
    column_name: str,
    fallback_key: str,
) -> str:
    if settings:
        try:
            value = settings[
                column_name
            ]
        except (
            KeyError,
            TypeError,
        ):
            value = None

        value = safe_text(
            value
        )

        if value:
            return value

    return DEFAULT_EMOJIS[
        fallback_key
    ]


# ============================================================
# TITLE
# ============================================================

def build_title(
    settings,
    token_name: str,
    token_symbol: str,
):
    custom_title = ""

    if settings:
        try:
            custom_title = safe_text(
                settings["alert_title"]
            )
        except (
            KeyError,
            TypeError,
        ):
            custom_title = ""

    if custom_title:
        title = custom_title
    else:
        title = "🚀 NEW BUY"

    token_name = safe_text(
        token_name,
        "Unknown Token",
    )

    token_symbol = safe_text(
        token_symbol,
        "TOKEN",
    )

    return (
        f"{title}\n"
        f"💎 {token_name} "
        f"(${token_symbol})"
    )


# ============================================================
# DEFAULT ALERT
# ============================================================

def build_default_alert(
    *,
    settings,
    chain,
    token_name,
    token_symbol,
    spent_usd=None,
    spent_native=None,
    native_symbol=None,
    received_amount=None,
    buyer=None,
    market_cap=None,
    tx_hash=None,
):
    buy_emoji = get_emoji(
        settings,
        "buy_emoji",
        "buy",
    )

    spent_emoji = get_emoji(
        settings,
        "spent_emoji",
        "spent",
    )

    received_emoji = get_emoji(
        settings,
        "received_emoji",
        "received",
    )

    holder_emoji = get_emoji(
        settings,
        "new_holder_emoji",
        "holder",
    )

    market_cap_emoji = get_emoji(
        settings,
        "market_cap_emoji",
        "market_cap",
    )

    network_emoji = get_emoji(
        settings,
        "network_emoji",
        "network",
    )

    title = build_title(
        settings,
        token_name,
        token_symbol,
    )

    token_symbol = safe_text(
        token_symbol,
        "TOKEN",
    )

    chain_text = chain_name(
        chain
    )

    lines = [
        title,
        "",
        f"{buy_emoji} BUY DETECTED",
    ]

    # --------------------------------------------------------
    # SPENT
    # --------------------------------------------------------

    if spent_usd is not None:
        lines.append(
            f"{spent_emoji} Spent: "
            f"{format_usd(spent_usd)}"
        )

    elif (
        spent_native is not None
        and native_symbol
    ):
        lines.append(
            f"{spent_emoji} Spent: "
            f"{format_token_amount("
            f"spent_native, "
            f"native_symbol"
            f")}"
        )

    else:
        lines.append(
            f"{spent_emoji} Spent: "
            f"Amount unavailable"
        )

    # --------------------------------------------------------
    # RECEIVED
    # --------------------------------------------------------

    if received_amount is not None:
        lines.append(
            f"{received_emoji} Received: "
            f"{format_token_amount("
            f"received_amount, "
            f"token_symbol"
            f")}"
        )

    # --------------------------------------------------------
    # HOLDER
    # --------------------------------------------------------

    if buyer:
        lines.append(
            f"{holder_emoji} Holder: "
            f"{short_address(buyer)}"
        )

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if market_cap is not None:
        lines.append(
            f"{market_cap_emoji} Market Cap: "
            f"{format_usd(market_cap)}"
        )

    # --------------------------------------------------------
    # NETWORK
    # --------------------------------------------------------

    lines.append(
        f"{network_emoji} Network: "
        f"{chain_text}"
    )

    # --------------------------------------------------------
    # TRANSACTION
    # --------------------------------------------------------

    if tx_hash:
        lines.append(
            f"{DEFAULT_EMOJIS['transaction']} "
            f"Transaction: "
            f"{short_address(tx_hash, 8, 6)}"
        )

    return "\n".join(
        lines
    )


# ============================================================
# CUSTOM TEMPLATE
# ============================================================

def apply_template(
    template: str,
    *,
    token_name,
    token_symbol,
    spent_usd=None,
    spent_native=None,
    native_symbol=None,
    received_amount=None,
    buyer=None,
    market_cap=None,
    chain=None,
    tx_hash=None,
):
    values = {
        "{name}": safe_text(
            token_name,
            "Unknown Token",
        ),
        "{symbol}": safe_text(
            token_symbol,
            "TOKEN",
        ),
        "{spent}": (
            format_usd(
                spent_usd
            )
            if spent_usd is not None
            else (
                format_token_amount(
                    spent_native,
                    native_symbol,
                )
                if (
                    spent_native is not None
                    and native_symbol
                )
                else "N/A"
            )
        ),
        "{received}": (
            format_token_amount(
                received_amount,
                safe_text(
                    token_symbol,
                    "TOKEN",
                ),
            )
            if received_amount is not None
            else "N/A"
        ),
        "{buyer}": short_address(
            buyer
        ),
        "{market_cap}": format_usd(
            market_cap
        ),
        "{chain}": chain_name(
            chain
        ),
        "{tx}": short_address(
            tx_hash,
            8,
            6,
        ),
    }

    result = template

    for key, value in values.items():
        result = result.replace(
            key,
            str(value),
        )

    return result


# ============================================================
# FINAL RENDER FUNCTION
# ============================================================

def render_buy_alert(
    *,
    settings,
    chain,
    token_name,
    token_symbol,
    spent_usd=None,
    spent_native=None,
    native_symbol=None,
    received_amount=None,
    buyer=None,
    market_cap=None,
    tx_hash=None,
):
    """
    Main renderer used by every BuyBot detector.

    Supported template variables:

        {name}
        {symbol}
        {spent}
        {received}
        {buyer}
        {market_cap}
        {chain}
        {tx}
    """

    template = ""

    if settings:
        try:
            template = safe_text(
                settings[
                    "alert_template"
                ]
            )
        except (
            KeyError,
            TypeError,
        ):
            template = ""

    if template:
        return (
            f"{build_title("
            f"settings, "
            f"token_name, "
            f"token_symbol"
            f")}\n\n"
            f"{apply_template("
            f"template, "
            f"token_name=token_name, "
            f"token_symbol=token_symbol, "
            f"spent_usd=spent_usd, "
            f"spent_native=spent_native, "
            f"native_symbol=native_symbol, "
            f"received_amount=received_amount, "
            f"buyer=buyer, "
            f"market_cap=market_cap, "
            f"chain=chain, "
            f"tx_hash=tx_hash"
            f")}"
        )

    return build_default_alert(
        settings=settings,
        chain=chain,
        token_name=token_name,
        token_symbol=token_symbol,
        spent_usd=spent_usd,
        spent_native=spent_native,
        native_symbol=native_symbol,
        received_amount=received_amount,
        buyer=buyer,
        market_cap=market_cap,
        tx_hash=tx_hash,
    )


# ============================================================
# COMPLETE KEYBOARD
# ============================================================

def build_buy_alert_keyboard(
    *,
    chain,
    tx_hash=None,
    token_address=None,
    dex_url=None,
    trending_url=None,
    custom_buttons=None,
):
    automatic = build_automatic_buttons(
        chain=chain,
        tx_hash=tx_hash,
        token_address=token_address,
        dex_url=dex_url,
        trending_url=trending_url,
    )

    custom = build_custom_buttons(
        custom_buttons
    )

    return arrange_buttons(
        automatic,
        custom,
    )


# ============================================================
# MEDIA + MESSAGE SENDER
# ============================================================

async def send_buy_alert(
    *,
    bot: Bot,
    group_id: int,
    settings,
    chain,
    token_name,
    token_symbol,
    spent_usd=None,
    spent_native=None,
    native_symbol=None,
    received_amount=None,
    buyer=None,
    market_cap=None,
    tx_hash=None,
    token_address=None,
    dex_url=None,
    trending_url=None,
    custom_buttons=None,
):
    """
    Central Telegram sender.

    Supports:
        - normal text
        - photo
        - animation/GIF
        - video
        - automatic buttons
        - custom buttons
    """

    text = render_buy_alert(
        settings=settings,
        chain=chain,
        token_name=token_name,
        token_symbol=token_symbol,
        spent_usd=spent_usd,
        spent_native=spent_native,
        native_symbol=native_symbol,
        received_amount=received_amount,
        buyer=buyer,
        market_cap=market_cap,
        tx_hash=tx_hash,
    )

    keyboard = build_buy_alert_keyboard(
        chain=chain,
        tx_hash=tx_hash,
        token_address=token_address,
        dex_url=dex_url,
        trending_url=trending_url,
        custom_buttons=custom_buttons,
    )

    media_type = None
    media_id = None

    if settings:
        try:
            media_type = safe_text(
                settings[
                    "media_type"
                ]
            )
        except (
            KeyError,
            TypeError,
        ):
            media_type = None

        try:
            media_id = safe_text(
                settings[
                    "media_id"
                ]
            )
        except (
            KeyError,
            TypeError,
        ):
            media_id = None

    if media_type == "photo" and media_id:
        await bot.send_photo(
            chat_id=group_id,
            photo=media_id,
            caption=text,
            reply_markup=keyboard,
        )

        return

    if (
        media_type == "animation"
        and media_id
    ):
        await bot.send_animation(
            chat_id=group_id,
            animation=media_id,
            caption=text,
            reply_markup=keyboard,
        )

        return

    if media_type == "video" and media_id:
        await bot.send_video(
            chat_id=group_id,
            video=media_id,
            caption=text,
            reply_markup=keyboard,
        )

        return

    await bot.send_message(
        chat_id=group_id,
        text=text,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


# ============================================================
# PUBLIC EXPORTS
# ============================================================

__all__ = [
    "CHAIN_CONFIG",
    "DEFAULT_EMOJIS",
    "normalize_chain",
    "chain_name",
    "transaction_url",
    "address_url",
    "format_number",
    "format_usd",
    "format_token_amount",
    "short_address",
    "build_automatic_buttons",
    "build_custom_buttons",
    "arrange_buttons",
    "render_buy_alert",
    "build_buy_alert_keyboard",
    "send_buy_alert",
]
