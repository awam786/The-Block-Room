from telegram import Bot
from telegram.error import TelegramError

from services.buybot_events import BuyEvent
from services.buybot_event_store import (
    event_already_processed,
    save_buybot_event,
)
from services.buybot_renderer import (
    render_buybot_alert,
)


async def send_buybot_alert(
    bot: Bot,
    event: BuyEvent,
) -> bool:
    """
    Render and send a BuyBot alert to the target group.

    Returns:
        True  -> alert was sent
        False -> alert was skipped or failed
    """

    try:
        # Prevent duplicate blockchain events.
        already_processed = (
            await event_already_processed(
                group_id=event.group_id,
                chain=event.chain,
                tx_hash=event.tx_hash,
                token_address=event.token_address,
            )
        )

        if already_processed:
            print(
                "BuyBot duplicate skipped: "
                f"{event.chain} / "
                f"{event.tx_hash}"
            )
            return False

        # Build the complete alert.
        alert = await render_buybot_alert(
            event
        )

        # BuyBot disabled or buy below minimum.
        if not alert:
            return False

        text = alert["text"]
        reply_markup = alert["reply_markup"]
        media_type = alert["media_type"]
        media_id = alert["media_id"]

        # -------------------------------------------------
        # SEND CUSTOM MEDIA
        # -------------------------------------------------

        if media_type and media_id:

            if media_type == "photo":
                await bot.send_photo(
                    chat_id=event.group_id,
                    photo=media_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

            elif media_type == "animation":
                await bot.send_animation(
                    chat_id=event.group_id,
                    animation=media_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

            elif media_type == "video":
                await bot.send_video(
                    chat_id=event.group_id,
                    video=media_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

            else:
                # Unknown media type:
                # fall back to normal text.
                await bot.send_message(
                    chat_id=event.group_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

        # -------------------------------------------------
        # SEND NORMAL TEXT ALERT
        # -------------------------------------------------

        else:
            await bot.send_message(
                chat_id=event.group_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )

        # Save ONLY after Telegram successfully accepted
        # the message. This allows a failed Telegram send
        # to be retried later.
        await save_buybot_event(
            event
        )

        print(
            "BuyBot alert sent: "
            f"group={event.group_id} "
            f"chain={event.chain} "
            f"token={event.token_symbol} "
            f"tx={event.tx_hash}"
        )

        return True

    except TelegramError as exc:
        print(
            "BuyBot Telegram error: "
            f"group={event.group_id} "
            f"chain={event.chain} "
            f"error={exc}"
        )

        return False

    except Exception as exc:
        print(
            "BuyBot dispatcher error: "
            f"group={event.group_id} "
            f"chain={event.chain} "
            f"error={exc}"
        )

        return False


async def dispatch_buy_event(
    bot: Bot,
    event: BuyEvent,
) -> bool:
    """
    Public dispatcher used by blockchain detectors.

    Example:

        await dispatch_buy_event(
            bot,
            event,
        )
    """

    return await send_buybot_alert(
        bot,
        event,
    )
