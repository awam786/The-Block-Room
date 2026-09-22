import os
from dotenv import load_dotenv

load_dotenv()


def required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


BOT_TOKEN = required_env("BOT_TOKEN")

DATABASE_URL = os.getenv("DATABASE_URL")

CMC_API_KEY = os.getenv("CMC_API_KEY")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
]

ADMIN_GROUP_ID = os.getenv("ADMIN_GROUP_ID")
TRENDING_CHANNEL_ID = os.getenv("TRENDING_CHANNEL_ID")
