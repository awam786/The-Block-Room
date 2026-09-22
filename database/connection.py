import asyncpg

from config import DATABASE_URL


_pool = None


async def init_db():
    global _pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not configured."
        )

    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
    )

    await create_tables()


async def close_db():
    global _pool

    if _pool:
        await _pool.close()
        _pool = None


def get_pool():
    if _pool is None:
        raise RuntimeError(
            "Database pool has not been initialized."
        )

    return _pool


async def create_tables():
    pool = get_pool()

    async with pool.acquire() as conn:

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_admin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS groups (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                title TEXT,
                username TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                buybot_enabled BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS pricing (
                duration_hours INTEGER PRIMARY KEY,
                price_usdt NUMERIC(20, 6) NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallets (
                chain TEXT PRIMARY KEY,
                address TEXT,
                token_symbol TEXT DEFAULT 'USDT',
                is_active BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                order_number TEXT UNIQUE NOT NULL,
                user_id BIGINT REFERENCES users(id),
                chain TEXT NOT NULL,
                contract_address TEXT NOT NULL,
                token_name TEXT,
                token_symbol TEXT,
                duration_hours INTEGER NOT NULL,
                amount_usdt NUMERIC(20, 6) NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                payment_chain TEXT,
                payment_tx_hash TEXT UNIQUE,
                payment_verified BOOLEAN DEFAULT FALSE,
                waiting_for_launch BOOLEAN DEFAULT FALSE,
                starts_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS trends (
                id BIGSERIAL PRIMARY KEY,
                order_id BIGINT UNIQUE REFERENCES orders(id),
                chain TEXT NOT NULL,
                contract_address TEXT NOT NULL,
                token_name TEXT,
                token_symbol TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',

                score NUMERIC(30, 10) DEFAULT 0,

                volume_24h NUMERIC(30, 10) DEFAULT 0,
                market_cap NUMERIC(30, 10) DEFAULT 0,
                liquidity NUMERIC(30, 10) DEFAULT 0,
                price_change_24h NUMERIC(30, 10) DEFAULT 0,

                buys_24h BIGINT DEFAULT 0,
                sells_24h BIGINT DEFAULT 0,

                rank INTEGER,

                paid_promotion BOOLEAN DEFAULT TRUE,

                last_activity_at TIMESTAMPTZ,
                last_volume_24h NUMERIC(30, 10) DEFAULT 0,

                inactivity_started_at TIMESTAMPTZ,

                started_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ,

                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id BIGSERIAL PRIMARY KEY,
                ticket_number TEXT UNIQUE NOT NULL,
                user_id BIGINT REFERENCES users(id),
                subject TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                admin_message TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS discounts (
                id BIGSERIAL PRIMARY KEY,
                duration_hours INTEGER NOT NULL,
                discounted_price_usdt NUMERIC(20, 6) NOT NULL,
                starts_at TIMESTAMPTZ DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                promo_text TEXT,
                promo_image TEXT,
                is_active BOOLEAN DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS broadcast_targets (
                id BIGSERIAL PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id BIGINT UNIQUE NOT NULL,
                title TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS used_payment_hashes (
                tx_hash TEXT PRIMARY KEY,
                chain TEXT NOT NULL,
                order_id BIGINT REFERENCES orders(id),
                used_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS buybot_settings (
                group_id BIGINT PRIMARY KEY,

                enabled BOOLEAN DEFAULT FALSE,

                min_buy_usd NUMERIC(30, 10)
                    DEFAULT 0,

                show_new_holder BOOLEAN
                    DEFAULT TRUE,

                show_market_cap BOOLEAN
                    DEFAULT TRUE,

                show_spent_amount BOOLEAN
                    DEFAULT TRUE,

                show_received_amount BOOLEAN
                    DEFAULT TRUE,

                custom_media_type TEXT,

                custom_media_file_id TEXT,

                buy_emoji TEXT
                    DEFAULT '🟢',

                spent_emoji TEXT
                    DEFAULT '🔀',

                received_emoji TEXT
                    DEFAULT '🪙',

                holder_emoji TEXT
                    DEFAULT '👤',

                market_cap_emoji TEXT
                    DEFAULT '💎',

                created_at TIMESTAMPTZ
                    DEFAULT NOW(),

                updated_at TIMESTAMPTZ
                    DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS buybot_buttons (
                id BIGSERIAL PRIMARY KEY,

                group_id BIGINT NOT NULL,

                position INTEGER NOT NULL,

                button_name TEXT NOT NULL,

                button_url TEXT NOT NULL,

                created_at TIMESTAMPTZ
                    DEFAULT NOW(),

                UNIQUE (
                    group_id,
                    position
                )
            );

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

                created_at TIMESTAMPTZ DEFAULT NOW(),

                updated_at TIMESTAMPTZ DEFAULT NOW(),

                UNIQUE (
                    group_id,
                    chain,
                    contract_address
                )
            );
            """
        )

        await conn.execute(
            """
            ALTER TABLE groups
            ADD COLUMN IF NOT EXISTS updated_at
                TIMESTAMPTZ DEFAULT NOW();

            ALTER TABLE trends
            ADD COLUMN IF NOT EXISTS buys_24h
                BIGINT DEFAULT 0;

            ALTER TABLE trends
            ADD COLUMN IF NOT EXISTS sells_24h
                BIGINT DEFAULT 0;

            ALTER TABLE trends
            ADD COLUMN IF NOT EXISTS rank
                INTEGER;

            ALTER TABLE trends
            ADD COLUMN IF NOT EXISTS paid_promotion
                BOOLEAN DEFAULT TRUE;

            ALTER TABLE trends
            ADD COLUMN IF NOT EXISTS last_activity_at
                TIMESTAMPTZ;

            ALTER TABLE trends
            ADD COLUMN IF NOT EXISTS last_volume_24h
                NUMERIC(30, 10) DEFAULT 0;

            ALTER TABLE trends
            ADD COLUMN IF NOT EXISTS inactivity_started_at
                TIMESTAMPTZ;
            """
        )

        await conn.executemany(
            """
            INSERT INTO settings (
                key,
                value
            )
            VALUES ($1, $2)
            ON CONFLICT (key)
            DO NOTHING
            """,
            [
                (
                    "payment_tolerance_usdt",
                    "0.10",
                ),
                (
                    "payment_confirmations",
                    "3",
                ),
                (
                    "trending_inactivity_minutes",
                    "10",
                ),
                (
                    "trending_update_seconds",
                    "30",
                ),
                (
                    "paid_start_rank_low_mc",
                    "5",
                ),
                (
                    "paid_start_rank_high_mc",
                    "3",
                ),
                (
                    "paid_high_mc_threshold",
                    "100000",
                ),
                (
                    "paid_top_mc_threshold",
                    "500000",
                ),
            ],
        )

        await conn.executemany(
            """
            INSERT INTO pricing (
                duration_hours,
                price_usdt
            )
            VALUES ($1, $2)
            ON CONFLICT (duration_hours)
            DO NOTHING
            """,
            [
                (2, 110),
                (6, 330),
                (12, 600),
                (24, 1000),
            ],
        )
