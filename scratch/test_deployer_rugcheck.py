import asyncio
import httpx

async def fetch_rugcheck(mint: str):
    url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("totalHolders", 0)
    except Exception as e:
        return f"Error: {e}"
    return 0

async def main():
    mints = ["FmjijgwEHpe32VPvHy1s7u7TLthh9yu1j75djVbWpump"]
    for m in mints:
        res = await fetch_rugcheck(m)
        print(f"Mint: {m} -> Holders: {res}")

asyncio.run(main())
