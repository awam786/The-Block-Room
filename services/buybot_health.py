from typing import Dict

from config import (
    BOT_TOKEN,
    HELIUS_API_KEY,
    ETHEREUM_RPC_URL,
    BNB_RPC_URL,
    ROBINHOOD_RPC_URL,
)

from database.connection import get_pool


async def check_database() -> bool:
    try:
        pool = await get_pool()

        async with pool.acquire() as connection:
            await connection.fetchval(
                "SELECT 1;"
            )

        return True

    except Exception as exc:
        print(
            "Database health check failed: "
            f"{exc}"
        )

        return False


def check_configuration() -> Dict[str, bool]:
    return {
        "bot_token": bool(
            BOT_TOKEN
        ),
        "helius_api": bool(
            HELIUS_API_KEY
        ),
        "ethereum_rpc": bool(
            ETHEREUM_RPC_URL
        ),
        "bnb_rpc": bool(
            BNB_RPC_URL
        ),
        "robinhood_rpc": bool(
            ROBINHOOD_RPC_URL
        ),
    }


async def get_buybot_health() -> dict:
    configuration = (
        check_configuration()
    )

    database_ok = (
        await check_database()
    )

    configuration_ok = all(
        configuration.values()
    )

    return {
        "database": database_ok,
        "configuration": configuration_ok,
        "ready": (
            database_ok
            and configuration_ok
        ),
        "details": configuration,
    }


async def print_buybot_health():
    health = (
        await get_buybot_health()
    )

    print(
        "========== BUYBOT HEALTH =========="
    )

    print(
        "Database: "
        + (
            "OK"
            if health["database"]
            else "FAILED"
        )
    )

    print(
        "Configuration: "
        + (
            "OK"
            if health["configuration"]
            else "FAILED"
        )
    )

    print(
        "Overall: "
        + (
            "READY"
            if health["ready"]
            else "NOT READY"
        )
    )

    for name, status in (
        health["details"]
        .items()
    ):
        print(
            f"{name}: "
            + (
                "OK"
                if status
                else "MISSING"
            )
        )

    print(
        "==================================="
    )
