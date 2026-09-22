from decimal import Decimal

from telegram import Bot

from config import BOT_TOKEN

from database.connection import get_pool

from services.buybot_events import (
    BuyEvent,
    normalize_buy_event,
)

from services.buybot_event_store import (
    save_buybot_event,
)

from services.buybot_renderer import (
    render_buybot_alert,
)


async def get_monitored_groups(
    chain: str,
    token_address: str,
):
    """
    Find every active group monitoring this token.
    """

    pool = get_pool()

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT
                bt.group_id,
                bt.token_name,
                bt.token_symbol,
                bt.pair_address,
                bt.dex_url,
                bs.enabled,
                bs.min_buy_usd
            FROM buybot_tokens bt

            LEFT JOIN buybot_settings bs
                ON bs.group_id = bt.group_id

            WHERE LOWER(bt.chain) = LOWER($1)
              AND LOWER(bt.contract_address) =
                  LOWER($2)
              AND bt.enabled = TRUE
              AND COALESCE(bs.enabled, TRUE) = TRUE;
            """,
            chain,
            token_address,
        )

        return rows


async def process_buy_event(
    event: BuyEvent,
):
    """
    Process one detected blockchain buy.

    Flow:

    Detector
        ↓
    Normalize event
        ↓
    Find monitored groups
        ↓
    Minimum-buy filter
        ↓
    Duplicate protection
        ↓
    Render alert
        ↓
    Send Telegram message
    """

    event = normalize_buy_event(event)

    if not event.tx_hash:
        return 0

    if not event.token_address:
        return 0

    groups = await get_monitored_groups(
        event.chain,
        event.token_address,
    )

    if not groups:
        return 0

    bot = Bot(
        token=BOT_TOKEN,
    )

    sent_count = 0

    for group in groups:

        group_id = group["group_id"]

        min_buy = (
            group["min_buy_usd"]
            or Decimal("0")
        )

        if (
            event.spent_amount_usd
            < Decimal(str(min_buy))
        ):
            continue

        token_name = (
            event.token_name
            or group["token_name"]
            or "Unknown Token"
        )

        token_symbol = (
            event.token_symbol
            or group["token_symbol"]
            or "TOKEN"
        )

        dex_url = (
            event.dex_url
            or group["dex_url"]
        )

        event_for_group = BuyEvent(
            group_id=group_id,
            chain=event.chain,
            tx_hash=event.tx_hash,
            token_address=event.token_address,
            token_name=token_name,
            token_symbol=token_symbol,
            buyer_address=event.buyer_address,
            spent_amount_usd=event.spent_amount_usd,
            spent_native_amount=event.spent_native_amount,
            spent_native_symbol=event.spent_native_symbol,
            received_amount=event.received_amount,
            received_symbol=event.received_symbol,
            market_cap_usd=event.market_cap_usd,
            is_new_holder=event.is_new_holder,
            dex_url=dex_url,
            buy_url=event.buy_url,
            trending_url=event.trending_url,
            block_number=event.block_number,
            timestamp=event.timestamp,
        )

        saved = await save_buybot_event(
            group_id=group_id,
            chain=event.chain,
            tx_hash=event.tx_hash,
            token_address=event.token_address,
            token_symbol=token_symbol,
            buyer_address=event.buyer_address,
            spent_amount_usd=event.spent_amount_usd,
            received_amount=event.received_amount,
        )

        if not saved:
            continue

        try:

            text, keyboard, media_type, media_id = (
                await render_buybot_alert(
                    event_for_group
                )
            )

            if media_type == "photo" and media_id:

                await bot.send_photo(
                    chat_id=group_id,
                    photo=media_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

            elif media_type == "video" and media_id:

                await bot.send_video(
                    chat_id=group_id,
                    video=media_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

            elif media_type == "animation" and media_id:

                await bot.send_animation(
                    chat_id=group_id,
                    animation=media_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

            else:

                await bot.send_message(
                    chat_id=group_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )

            sent_count += 1

        except Exception as exc:

            print(
                "BuyBot send error "
                f"group={group_id} "
                f"tx={event.tx_hash}: "
                f"{exc}"
            )

    return sent_count
