from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from database.connection import get_pool
from services.buybot_events import BuyEvent


# =========================================================
# DEFAULT BUYBOT VALUES
# =========================================================

DEFAULT_TITLE = "🚨 Fresh Buy"

DEFAULT_TEMPLATE = (
    "{buy_emoji} *{token_name}* "
    "({token_symbol})\n\n"
    "{spent_emoji} Spent: *${spent_usd}*\n"
    "{received_emoji} Received: "
    "*{received_amount} {received_symbol}*\n\n"
    "{holder_emoji} {holder_status}\n"
    "{market_cap_emoji} Market Cap: *${market_cap}*\n"
    "{network_emoji} Network: *{chain}*"
)

DEFAULT_EMOJIS = {
    "buy_emoji": "🟢",
    "holder_emoji": "👤",
    "market_cap_emoji": "💎",
    "spent_emoji": "💸",
    "received_emoji": "🪙",
    "network_emoji": "⛓️",
}


# =========================================================
# HELPERS
# =========================================================


def safe_number(
    value,
    decimals: int = 2,
) -> str:
    try:
        number = float(value or 0)

        return f"{number:,.{decimals}f}"

    except (
        TypeError,
        ValueError,
    ):
        return "0"


def safe_text(
    value,
    fallback: str = "-",
) -> str:
    if value is None:
        return fallback

    text = str(value).strip()

    return text or fallback


def escape_markdown_v2(
    text: str,
) -> str:
    """
    Escape Telegram MarkdownV2 special characters.
    """

    special = (
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    )

    result = str(text)

    for char in special:
        result = result.replace(
            char,
            "\\" + char,
        )

    return result


# =========================================================
# SETTINGS
# =========================================================


async def get_buybot_settings(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        return await connection.fetchrow(
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
            WHERE group_id = $1;
            """,
            group_id,
        )


async def get_custom_buttons(
    group_id: int,
):
    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                button_name,
                button_url,
                position
            FROM buybot_buttons
            WHERE group_id = $1
            ORDER BY position ASC, id ASC
            LIMIT 3;
            """,
            group_id,
        )

    return rows


# =========================================================
# TEMPLATE VALUES
# =========================================================


def event_values(
    event: BuyEvent,
    settings,
):
    holder_status = (
        "New Holder"
        if event.is_new_holder
        else "Existing Holder"
    )

    values = {
        "token_name": safe_text(
            event.token_name,
            "Unknown Token",
        ),
        "token_symbol": safe_text(
            event.token_symbol,
            "?",
        ),
        "chain": safe_text(
            event.chain,
            "Unknown",
        ).upper(),

        "spent_usd": safe_number(
            event.spent_amount_usd,
            2,
        ),

        "spent_native": safe_number(
            event.spent_native_amount,
            6,
        ),

        "native_symbol": safe_text(
            event.spent_native_symbol,
            "",
        ),

        "received_amount": safe_number(
            event.received_amount,
            2,
        ),

        "received_symbol": safe_text(
            event.received_symbol
            or event.token_symbol,
            "?",
        ),

        "market_cap": safe_number(
            event.market_cap_usd,
            0,
        ),

        "buyer": safe_text(
            event.buyer_address,
            "-",
        ),

        "holder_status": holder_status,

        "buy_emoji": (
            settings["buy_emoji"]
            or DEFAULT_EMOJIS["buy_emoji"]
        ),

        "holder_emoji": (
            settings["new_holder_emoji"]
            or DEFAULT_EMOJIS["holder_emoji"]
        ),

        "market_cap_emoji": (
            settings["market_cap_emoji"]
            or DEFAULT_EMOJIS[
                "market_cap_emoji"
            ]
        ),

        "spent_emoji": (
            settings["spent_emoji"]
            or DEFAULT_EMOJIS["spent_emoji"]
        ),

        "received_emoji": (
            settings["received_emoji"]
            or DEFAULT_EMOJIS[
                "received_emoji"
            ]
        ),

        "network_emoji": (
            settings["network_emoji"]
            or DEFAULT_EMOJIS[
                "network_emoji"
            ]
        ),
    }

    return values


# =========================================================
# RENDER ALERT TEXT
# =========================================================


def render_alert_text(
    event: BuyEvent,
    settings,
) -> str:
    title = (
        settings["alert_title"]
        or DEFAULT_TITLE
    )

    template = (
        settings["alert_template"]
        or DEFAULT_TEMPLATE
    )

    values = event_values(
        event,
        settings,
    )

    try:
        body = template.format(
            **values
        )

    except Exception as exc:
        print(
            "BuyBot template error: "
            f"{exc}"
        )

        body = DEFAULT_TEMPLATE.format(
            **values
        )

    return (
        f"*{title}*\n\n"
        f"{body}"
    )


# =========================================================
# AUTOMATIC BUTTONS
# =========================================================


def automatic_buttons(
    event: BuyEvent,
):
    buttons = []

    if event.dex_url:
        buttons.append(
            InlineKeyboardButton(
                "📊 Chart",
                url=event.dex_url,
            )
        )

    if event.buy_url:
        buttons.append(
            InlineKeyboardButton(
                "🛒 Buy",
                url=event.buy_url,
            )
        )

    if event.trending_url:
        buttons.append(
            InlineKeyboardButton(
                "🔥 Trending",
                url=event.trending_url,
            )
        )

    return buttons


# =========================================================
# CUSTOM BUTTONS
# =========================================================


def custom_buttons(
    rows,
):
    buttons = []

    for row in rows[:3]:
        name = str(
            row["button_name"]
        ).strip()

        url = str(
            row["button_url"]
        ).strip()

        if not name or not url:
            continue

        buttons.append(
            InlineKeyboardButton(
                name,
                url=url,
            )
        )

    return buttons


# =========================================================
# KEYBOARD
# =========================================================


async def build_buybot_keyboard(
    event: BuyEvent,
    group_id: int,
):
    custom = await get_custom_buttons(
        group_id
    )

    auto_buttons = automatic_buttons(
        event
    )

    custom_buttons_list = custom_buttons(
        custom
    )

    all_buttons = (
        auto_buttons
        + custom_buttons_list
    )

    if not all_buttons:
        return None

    rows = []

    # Automatic buttons
    if auto_buttons:
        rows.append(
            auto_buttons
        )

    # Custom buttons
    for button in custom_buttons_list:
        rows.append(
            [button]
        )

    return InlineKeyboardMarkup(
        rows
    )


# =========================================================
# MEDIA INFORMATION
# =========================================================


async def get_buybot_media(
    group_id: int,
):
    settings = await get_buybot_settings(
        group_id
    )

    if not settings:
        return None, None

    media_type = settings[
        "media_type"
    ]

    media_id = settings[
        "media_id"
    ]

    if not media_type or not media_id:
        return None, None

    return (
        media_type,
        media_id,
    )


# =========================================================
# COMPLETE RENDER
# =========================================================


async def render_buybot_alert(
    event: BuyEvent,
):
    """
    Returns everything required to send
    a BuyBot alert.

    Result:

        {
            "text": "...",
            "reply_markup": ...,
            "media_type": "...",
            "media_id": "..."
        }
    """

    settings = await get_buybot_settings(
        event.group_id
    )

    if not settings:
        return None

    if not settings["enabled"]:
        return None

    # -----------------------------------------------------
    # Minimum buy filter
    # -----------------------------------------------------

    minimum = float(
        settings["min_buy_usd"]
        or 0
    )

    spent_usd = float(
        event.spent_amount_usd
        or 0
    )

    if (
        minimum > 0
        and spent_usd < minimum
    ):
        return None

    # -----------------------------------------------------
    # Text
    # -----------------------------------------------------

    text = render_alert_text(
        event,
        settings,
    )

    # -----------------------------------------------------
    # Buttons
    # -----------------------------------------------------

    keyboard = await build_buybot_keyboard(
        event,
        event.group_id,
    )

    # -----------------------------------------------------
    # Media
    # -----------------------------------------------------

    media_type = settings[
        "media_type"
    ]

    media_id = settings[
        "media_id"
    ]

    return {
        "text": text,
        "reply_markup": keyboard,
        "media_type": media_type,
        "media_id": media_id,
    }


# =========================================================
# SIMPLE PREVIEW DATA
# =========================================================


def preview_event():
    return BuyEvent(
        group_id=0,
        chain="bnb",
        tx_hash="preview",
        token_address="0x0000000000000000000000000000000000000000",
        token_name="Example Token",
        token_symbol="EXM",
        buyer_address="0x1234...5678",
        spent_amount_usd=125.40,
        spent_native_amount=0.21,
        spent_native_symbol="BNB",
        received_amount=12450,
        received_symbol="EXM",
        market_cap_usd=84500,
        is_new_holder=True,
        dex_url="https://dexscreener.com/",
        buy_url="https://dexscreener.com/",
        trending_url=None,
        block_number=None,
        timestamp=None,
    )
