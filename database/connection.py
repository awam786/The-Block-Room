import asyncpg

from config import DATABASE_URL


_pool = None


async def init_db():
    global _pool

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")

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
        raise RuntimeError("Database pool has not been initialized.")

    return _pool


async def create_tables():
    pool = get_pool()

    async with pool.acquire() as conn:
        await conn.execute("""
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
                created_at TIMESTAMPTZ DEFAULT NOW()
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
        """)

        # Default pricing
        await conn.executemany(
            """
            INSERT INTO pricing (duration_hours, price_usdt)
            VALUES ($1, $2)
            ON CONFLICT (duration_hours) DO NOTHING
            """,
            [
                (2, 110),
                (6, 330),
                (12, 600),
                (24, 1000),
            ],
        )
