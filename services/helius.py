import httpx

from config import HELIUS_API_KEY


HELIUS_URL = "https://mainnet.helius-rpc.com/"


async def get_solana_asset(mint_address: str):
    if not HELIUS_API_KEY:
        return None

    payload = {
        "jsonrpc": "2.0",
        "id": "the-block-room",
        "method": "getAsset",
        "params": {
            "id": mint_address,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                HELIUS_URL,
                params={"api-key": HELIUS_API_KEY},
                json=payload,
            )

            response.raise_for_status()
            data = response.json()

    except (httpx.HTTPError, ValueError):
        return None

    return data.get("result")
