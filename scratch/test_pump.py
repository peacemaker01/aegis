import asyncio
import httpx
import json

async def main():
    mint = "F5tfztTnE4sYsMhZT5KrFpWvHmYSfJZoRjCuxKPbpump"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"https://frontend-api-v3.pump.fun/coins/{mint}", headers=headers)
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            print(json.dumps(r.json(), indent=2))
        else:
            print(r.text[:500])

asyncio.run(main())
