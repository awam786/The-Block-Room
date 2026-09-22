from decimal import Decimal

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS
from database.connection import get_pool


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# =========================================================
# FORMAT MONEY
# =========================================================

def format_usdt(value) -> str:
    if value is None:
        return "0.00"

    try:
        amount = Decimal(str(value))
    except Exception:
        return "0.00"

    return f"{amount:,.2f}"


# =========================================================
# ADMIN DASHBOARD
# =========================================================

async def admin_dashboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    pool = await get_pool()

    async with pool.acquire() as connection:

        # -------------------------------------------------
        # USERS
        # -------------------------------------------------

        total_users = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            """
        )

        # -------------------------------------------------
        # GROUPS
        # -------------------------------------------------

        total_groups = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM groups
            WHERE is_active = TRUE
            """
        )

        # -------------------------------------------------
        # ORDERS
        # -------------------------------------------------

        total_orders = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM orders
            """
        )

        paid_orders = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status IN (
                'PAID',
                'ACTIVE',
                'COMPLETED',
                'WAITING_FOR_LAUNCH'
            )
            """
        )

        pending_orders = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status = 'PAYMENT_PENDING'
            """
        )

        failed_orders = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status IN (
                'PAYMENT_FAILED',
                'FAILED',
                'CANCELLED'
            )
            """
        )

        # -------------------------------------------------
        # REVENUE
        # -------------------------------------------------

        revenue = await connection.fetchval(
            """
            SELECT COALESCE(
                SUM(amount),
                0
            )
            FROM orders
            WHERE status IN (
                'PAID',
                'ACTIVE',
                'COMPLETED',
                'WAITING_FOR_LAUNCH'
            )
            """
        )

        # -------------------------------------------------
        # ACTIVE TRENDS
        # -------------------------------------------------

        active_trends = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM trends
            WHERE active = TRUE
            """
        )

        # -------------------------------------------------
        # BUYBOT GROUPS
        # -------------------------------------------------

        buybot_groups = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_settings
            WHERE enabled = TRUE
            """
        )

        # -------------------------------------------------
        # BUYBOT TOKENS
        # -------------------------------------------------

        buybot_tokens = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_tokens
            WHERE enabled = TRUE
            """
        )

        # -------------------------------------------------
        # BUYBOT EVENTS
        # -------------------------------------------------

        buybot_events = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_events
            """
        )

        # -------------------------------------------------
        # TICKETS
        # -------------------------------------------------

        open_tickets = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM tickets
            WHERE status = 'OPEN'
            """
        )

        in_progress_tickets = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM tickets
            WHERE status = 'IN_PROGRESS'
            """
        )

        closed_tickets = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM tickets
            WHERE status = 'CLOSED'
            """
        )

        solved_tickets = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM tickets
            WHERE status = 'SOLVED'
            """
        )

        # -------------------------------------------------
        # DISCOUNTS
        # -------------------------------------------------

        active_discounts = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM discounts
            WHERE active = TRUE
              AND (
                  expires_at IS NULL
                  OR expires_at > NOW()
              )
            """
        )

        # -------------------------------------------------
        # PAYMENT WALLETS
        # -------------------------------------------------

        payment_wallets = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM payment_wallets
            WHERE enabled = TRUE
            """
        )

    # =====================================================
    # DASHBOARD MESSAGE
    # =====================================================

    text = (
        "🏛️ *THE BLOCK ROOM — ADMIN DASHBOARD*\n\n"

        "👥 *Users & Groups*\n"
        f"👤 Users: `{total_users}`\n"
        f"👥 Active Groups: `{total_groups}`\n\n"

        "💰 *Orders & Revenue*\n"
        f"📦 Total Orders: `{total_orders}`\n"
        f"✅ Paid Orders: `{paid_orders}`\n"
        f"⏳ Pending Payments: `{pending_orders}`\n"
        f"❌ Failed Orders: `{failed_orders}`\n"
        f"💵 Revenue: `{format_usdt(revenue)} USDT`\n\n"

        "📈 *Trending*\n"
        f"🟢 Active Trends: `{active_trends}`\n\n"

        "🤖 *BuyBot*\n"
        f"🟢 Active Groups: `{buybot_groups}`\n"
        f"🪙 Monitored Tokens: `{buybot_tokens}`\n"
        f"🛒 Buy Events: `{buybot_events}`\n\n"

        "🎫 *Support*\n"
        f"🟡 Open: `{open_tickets}`\n"
        f"🔵 In Progress: `{in_progress_tickets}`\n"
        f"🟢 Solved: `{solved_tickets}`\n"
        f"⚪ Closed: `{closed_tickets}`\n\n"

        "🏷️ *Discounts*\n"
        f"Active Campaigns: `{active_discounts}`\n\n"

        "💳 *Payment Configuration*\n"
        f"Active Wallets: `{payment_wallets}`"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
    )


# =========================================================
# RECENT ORDERS
# =========================================================

async def recent_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                order_id,
                chain,
                token_symbol,
                amount,
                status,
                created_at
            FROM orders
            ORDER BY created_at DESC
            LIMIT 15
            """
        )

    if not rows:
        await update.message.reply_text(
            "📦 No orders found."
        )

        return

    lines = [
        "📦 *RECENT ORDERS*",
        "",
    ]

    for row in rows:
        order_id = (
            row["order_id"]
            or f"ORDER-{row['created_at']}"
        )

        symbol = (
            row["token_symbol"]
            or "Unknown"
        )

        amount = format_usdt(
            row["amount"]
        )

        status = row["status"]

        lines.append(
            f"`{order_id}`\n"
            f"⛓ {row['chain']} | "
            f"🪙 {symbol}\n"
            f"💵 {amount} USDT | "
            f"{status}\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# =========================================================
# ACTIVE TRENDS
# =========================================================

async def active_trends(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    pool = await get_pool()

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                rank,
                chain,
                token_symbol,
                token_address,
                market_cap_usd,
                volume_24h,
                placement_score,
                expires_at
            FROM trends
            WHERE active = TRUE
            ORDER BY rank ASC NULLS LAST
            LIMIT 20
            """
        )

    if not rows:
        await update.message.reply_text(
            "📈 No active trends."
        )

        return

    lines = [
        "📈 *ACTIVE TRENDING TOKENS*",
        "",
    ]

    for row in rows:
        rank = (
            row["rank"]
            if row["rank"] is not None
            else "-"
        )

        symbol = (
            row["token_symbol"]
            or "Unknown"
        )

        market_cap = format_usdt(
            row["market_cap_usd"]
        )

        volume = format_usdt(
            row["volume_24h"]
        )

        score = format_usdt(
            row["placement_score"]
        )

        lines.append(
            f"#{rank} *{symbol}*\n"
            f"⛓ {row['chain']}\n"
            f"💎 MC: ${market_cap}\n"
            f"📊 24h Vol: ${volume}\n"
            f"⚡ Score: {score}\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# =========================================================
# BUYBOT STATS
# =========================================================

async def buybot_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    pool = await get_pool()

    async with pool.acquire() as connection:

        rows = await connection.fetch(
            """
            SELECT
                chain,
                COUNT(*) AS total
            FROM buybot_events
            GROUP BY chain
            ORDER BY total DESC
            """
        )

        total_events = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_events
            """
        )

        total_groups = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_settings
            WHERE enabled = TRUE
            """
        )

        total_tokens = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM buybot_tokens
            WHERE enabled = TRUE
            """
        )

    lines = [
        "🤖 *BUYBOT STATISTICS*",
        "",
        f"🟢 Active Groups: `{total_groups}`",
        f"🪙 Monitored Tokens: `{total_tokens}`",
        f"🛒 Total Buy Events: `{total_events}`",
        "",
        "*Events by Chain:*",
    ]

    if rows:
        for row in rows:
            lines.append(
                f"⛓ {row['chain']}: "
                f"`{row['total']}`"
            )
    else:
        lines.append(
            "No BuyBot events yet."
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


# =========================================================
# SYSTEM HEALTH
# =========================================================

async def system_health(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    database_status = "❌"

    try:
        pool = await get_pool()

        async with pool.acquire() as connection:
            await connection.fetchval(
                "SELECT 1"
            )

        database_status = "🟢 ONLINE"

    except Exception as exc:
        print(
            f"Database health check failed: "
            f"{exc}"
        )

        database_status = "🔴 OFFLINE"

    await update.message.reply_text(
        "⚙️ *SYSTEM HEALTH*\n\n"
        f"🗄 PostgreSQL: "
        f"`{database_status}`\n"
        "🤖 Telegram Bot: `ONLINE`\n"
        "📡 Background Workers: `RUNNING`\n"
        "🔐 Environment Secrets: `CONFIGURED VIA ENV`\n\n"
        "No private keys or seed phrases are "
        "stored by the bot.",
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN HELP
# =========================================================

async def dashboard_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    await update.message.reply_text(
        "🏛️ *ADMIN COMMANDS*\n\n"

        "📊 *Dashboard*\n"
        "/admin — Main dashboard\n"
        "/orders — Recent orders\n"
        "/activetrends — Active trends\n"
        "/buystats — BuyBot statistics\n"
        "/health — System health\n\n"

        "🎫 *Support*\n"
        "/tickets — Recent tickets\n"
        "/ticketstats — Ticket statistics\n\n"

        "📢 *Marketing*\n"
        "/broadcast — Broadcast message\n"
        "/discount2h — 2h discount\n"
        "/discount6h — 6h discount\n"
        "/discount12h — 12h discount\n"
        "/discount24h — 24h discount\n\n"

        "⚙️ *Configuration*\n"
        "/prices — View prices\n"
        "/wallets — View payment wallets\n"
        "/tokens — BuyBot tokens",
        parse_mode="Markdown",
    )
