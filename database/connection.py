import asyncpg

from config import DATABASE_URL


_pool = None


async def init_db():
    global _pool

    if _pool is not None:
        return

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not configured."
        )

    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
        command_timeout=30,
    )

    async with _pool.acquire() as connection:

        # =========================================================
        # USERS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # GROUPS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS groups (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                title TEXT,
                username TEXT,
                type TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # PRICING
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pricing (
                id BIGSERIAL PRIMARY KEY,
                duration_hours INTEGER UNIQUE NOT NULL,
                price NUMERIC(30, 10) NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # PAYMENT WALLETS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_wallets (
                id BIGSERIAL PRIMARY KEY,
                chain TEXT UNIQUE NOT NULL,
                wallet_address TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # ORDERS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,

                order_id TEXT UNIQUE,

                user_id BIGINT,

                chain TEXT NOT NULL,

                token_address TEXT NOT NULL,

                token_name TEXT,

                token_symbol TEXT,

                duration_hours INTEGER NOT NULL,

                amount NUMERIC(30, 10) NOT NULL,

                payment_wallet TEXT,

                transaction_hash TEXT,

                status TEXT NOT NULL DEFAULT
                    'PAYMENT_PENDING',

                payment_error TEXT,

                created_at TIMESTAMPTZ DEFAULT NOW(),

                updated_at TIMESTAMPTZ DEFAULT NOW(),

                paid_at TIMESTAMPTZ,

                activated_at TIMESTAMPTZ,

                expires_at TIMESTAMPTZ
            );
            """
        )

        # =========================================================
        # ORDER COMPATIBILITY COLUMNS
        # =========================================================

        await connection.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
                order_id TEXT;
            """
        )

        await connection.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
                user_id BIGINT;
            """
        )

        await connection.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
                payment_wallet TEXT;
            """
        )

        await connection.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
                payment_error TEXT;
            """
        )

        await connection.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
                updated_at TIMESTAMPTZ
                DEFAULT NOW();
            """
        )

        await connection.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
                paid_at TIMESTAMPTZ;
            """
        )

        await connection.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
                activated_at TIMESTAMPTZ;
            """
        )

        await connection.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
                expires_at TIMESTAMPTZ;
            """
        )

        # =========================================================
        # USED PAYMENT HASHES
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS used_payment_hashes (
                id BIGSERIAL PRIMARY KEY,
                tx_hash TEXT UNIQUE NOT NULL,
                order_id TEXT,
                chain TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # TRENDS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trends (
                id BIGSERIAL PRIMARY KEY,

                order_id TEXT,

                chain TEXT NOT NULL,

                token_address TEXT NOT NULL,

                token_name TEXT,

                token_symbol TEXT,

                pair_address TEXT,

                dex_url TEXT,

                active BOOLEAN DEFAULT TRUE,

                paid_promotion BOOLEAN DEFAULT TRUE,

                natural_score NUMERIC(30, 10)
                    DEFAULT 0,

                placement_score NUMERIC(30, 10)
                    DEFAULT 0,

                rank INTEGER,

                volume_24h NUMERIC(40, 10)
                    DEFAULT 0,

                liquidity_usd NUMERIC(40, 10)
                    DEFAULT 0,

                market_cap_usd NUMERIC(40, 10)
                    DEFAULT 0,

                buy_activity NUMERIC(40, 10)
                    DEFAULT 0,

                price_momentum NUMERIC(30, 10)
                    DEFAULT 0,

                recent_activity NUMERIC(30, 10)
                    DEFAULT 0,

                last_activity_at TIMESTAMPTZ,

                started_at TIMESTAMPTZ
                    DEFAULT NOW(),

                expires_at TIMESTAMPTZ,

                created_at TIMESTAMPTZ
                    DEFAULT NOW(),

                updated_at TIMESTAMPTZ
                    DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # TICKETS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id BIGSERIAL PRIMARY KEY,

                ticket_id TEXT UNIQUE NOT NULL,

                user_id BIGINT,

                status TEXT DEFAULT 'OPEN',

                subject TEXT,

                description TEXT,

                admin_message TEXT,

                created_at TIMESTAMPTZ
                    DEFAULT NOW(),

                updated_at TIMESTAMPTZ
                    DEFAULT NOW(),

                closed_at TIMESTAMPTZ
            );
            """
        )

        # =========================================================
        # DISCOUNTS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS discounts (
                id BIGSERIAL PRIMARY KEY,

                duration_hours INTEGER UNIQUE NOT NULL,

                discount_price NUMERIC(30, 10)
                    NOT NULL,

                active BOOLEAN DEFAULT TRUE,

                promo_text TEXT,

                promo_media_type TEXT,

                promo_media_id TEXT,

                starts_at TIMESTAMPTZ
                    DEFAULT NOW(),

                expires_at TIMESTAMPTZ,

                created_at TIMESTAMPTZ
                    DEFAULT NOW(),

                updated_at TIMESTAMPTZ
                    DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # BROADCAST TARGETS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_targets (
                id BIGSERIAL PRIMARY KEY,

                target_id BIGINT UNIQUE NOT NULL,

                target_type TEXT NOT NULL,

                title TEXT,

                active BOOLEAN DEFAULT TRUE,

                created_at TIMESTAMPTZ
                    DEFAULT NOW(),

                updated_at TIMESTAMPTZ
                    DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # SYSTEM SETTINGS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS system_settings (
                setting_key TEXT PRIMARY KEY,

                setting_value TEXT NOT NULL,

                description TEXT,

                updated_at TIMESTAMPTZ
                    DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # DEFAULT PAYMENT SETTINGS
        # =========================================================

        await connection.execute(
            """
            INSERT INTO system_settings (
                setting_key,
                setting_value,
                description
            )
            VALUES (
                'payment_tolerance_usdt',
                '0.10',
                'Maximum accepted USDT payment difference.'
            )
            ON CONFLICT (
                setting_key
            )
            DO NOTHING;
            """
        )

        # =========================================================
        # BUYBOT SETTINGS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS buybot_settings (
                group_id BIGINT PRIMARY KEY,

                enabled BOOLEAN DEFAULT FALSE,

                min_buy_usd NUMERIC(30, 10)
                    DEFAULT 0,

                media_type TEXT,

                media_id TEXT,

                alert_title TEXT,

                alert_template TEXT,

                buy_emoji TEXT,

                new_holder_emoji TEXT,

                market_cap_emoji TEXT,

                spent_emoji TEXT,

                received_emoji TEXT,

                network_emoji TEXT,

                updated_at TIMESTAMPTZ
                    DEFAULT NOW()
            );
            """
        )

        # =========================================================
        # BUYBOT BUTTONS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS buybot_buttons (
                id BIGSERIAL PRIMARY KEY,

                group_id BIGINT NOT NULL,

                button_name TEXT NOT NULL,

                button_url TEXT NOT NULL,

                position INTEGER DEFAULT 0,

                created_at TIMESTAMPTZ
                    DEFAULT NOW()
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_buybot_buttons_group
            ON buybot_buttons (
                group_id,
                position
            );
            """
        )

        # =========================================================
        # BUYBOT TOKENS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS buybot_tokens (
                id BIGSERIAL PRIMARY KEY,

                group_id BIGINT NOT NULL,

                chain TEXT NOT NULL,

                contract_address TEXT NOT NULL,

                token_name TEXT,

                token_symbol TEXT,

                pair_address TEXT,

                dex_url TEXT,

                enabled BOOLEAN DEFAULT TRUE,

                created_at TIMESTAMPTZ
                    DEFAULT NOW(),

                updated_at TIMESTAMPTZ
                    DEFAULT NOW(),

                UNIQUE (
                    group_id,
                    chain,
                    contract_address
                )
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_buybot_tokens_group
            ON buybot_tokens (
                group_id
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_buybot_tokens_chain_address
            ON buybot_tokens (
                chain,
                contract_address
            );
            """
        )

        # =========================================================
        # BUYBOT EVENTS
        # =========================================================

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS buybot_events (
                id BIGSERIAL PRIMARY KEY,

                group_id BIGINT NOT NULL,

                chain TEXT NOT NULL,

                tx_hash TEXT NOT NULL,

                token_address TEXT NOT NULL,

                token_symbol TEXT,

                buyer_address TEXT,

                spent_amount_usd NUMERIC(30, 10),

                received_amount NUMERIC(40, 10),

                created_at TIMESTAMPTZ
                    DEFAULT NOW(),

                UNIQUE (
                    group_id,
                    chain,
                    tx_hash,
                    token_address
                )
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_buybot_events_group_created
            ON buybot_events (
                group_id,
                created_at DESC
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_buybot_events_token
            ON buybot_events (
                chain,
                token_address
            );
            """
        )

        # =========================================================
        # DEFAULT PRICES
        # =========================================================

        await connection.execute(
            """
            INSERT INTO pricing (
                duration_hours,
                price
            )
            VALUES
                (2, 110),
                (6, 330),
                (12, 600),
                (24, 1000)
            ON CONFLICT (
                duration_hours
            )
            DO NOTHING;
            """
        )

        # =========================================================
        # DEFAULT BUYBOT SETTINGS
        # =========================================================

        await connection.execute(
            """
            INSERT INTO buybot_settings (
                group_id,
                enabled,
                min_buy_usd
            )
            SELECT
                g.telegram_id,
                FALSE,
                0
            FROM groups g
            WHERE NOT EXISTS (
                SELECT 1
                FROM buybot_settings bs
                WHERE bs.group_id =
                    g.telegram_id
            );
            """
        )

        # =========================================================
        # INDEXES
        # =========================================================

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_orders_user
            ON orders (
                user_id
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_orders_status
            ON orders (
                status
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_orders_tx_hash
            ON orders (
                transaction_hash
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_trends_active
            ON trends (
                active,
                rank
            );
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_tickets_user_status
            ON tickets (
                user_id,
                status
            );
            """
        )

    print(
        "Database schema initialized successfully."
    )


async def get_pool():
    if _pool is None:
        await init_db()

    return _pool


async def close_db():
    global _pool

    if _pool is not None:
        await _pool.close()

        _pool = None

        print(
            "Database pool closed."
        )
