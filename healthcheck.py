import asyncio
import importlib
import sys


MODULES = [
    "config",
    "database.connection",

    "handlers.start",
    "handlers.admin",
    "handlers.pricing",
    "handlers.wallets",
    "handlers.buybot",
    "handlers.trending",

    "services.dex",
    "services.helius",
    "services.token_validator",
    "services.orders",
    "services.payment_verifier",
    "services.payment_worker",
    "services.trend_activation_worker",
    "services.trending_engine",
    "services.trending_publisher",

    "services.buybot_events",
    "services.buybot_event_store",
    "services.buybot_renderer",
    "services.buybot_dispatcher",
    "services.buybot_enrichment",
    "services.buybot_holder",

    "services.evm_metadata",
    "services.evm_detector",
    "services.solana_detector",
    "services.buybot_health",
]


def check_imports():
    failed = []

    print()
    print("=" * 60)
    print("THE BLOCK ROOM — IMPORT CHECK")
    print("=" * 60)
    print()

    for module_name in MODULES:
        try:
            importlib.import_module(
                module_name
            )

            print(
                f"✅ {module_name}"
            )

        except Exception as exc:
            print(
                f"❌ {module_name}"
            )

            print(
                f"   {type(exc).__name__}: "
                f"{exc}"
            )

            failed.append(
                (
                    module_name,
                    exc,
                )
            )

    print()
    print("=" * 60)

    if failed:
        print(
            f"FAILED: {len(failed)} module(s)"
        )

        print()
        print(
            "Fix the errors above before "
            "deploying to Railway."
        )

        return False

    print(
        "ALL MODULE IMPORTS PASSED."
    )

    print(
        "The Python module structure is OK."
    )

    return True


async def check_database():
    try:
        from database.connection import (
            init_db,
            close_db,
        )

        print()
        print(
            "=" * 60
        )
        print(
            "DATABASE CONNECTION CHECK"
        )
        print(
            "=" * 60
        )

        await init_db()

        print(
            "✅ PostgreSQL connection successful."
        )

        await close_db()

        print(
            "✅ PostgreSQL connection closed."
        )

        return True

    except Exception as exc:
        print(
            "❌ PostgreSQL check failed."
        )

        print(
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return False


async def main():
    imports_ok = check_imports()

    if not imports_ok:
        sys.exit(1)

    database_ok = await check_database()

    if not database_ok:
        sys.exit(1)

    print()
    print("=" * 60)
    print(
        "🎉 THE BLOCK ROOM HEALTH CHECK PASSED"
    )
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(
        main()
    )
