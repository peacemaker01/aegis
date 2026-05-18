import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from fetchers.nansen import NansenClient

async def test():
    key = os.getenv("NANSEN_API_KEY", "")
    print(f"Key: {key[:8]}...")
    import httpx
    
    address = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
    headers = {"apikey": key, "Content-Type": "application/json"}
    payload = {"address": address, "chain": "ethereum"}
    
    async with httpx.AsyncClient() as client:
        # Test Label
        url = "https://api.nansen.ai/api/v1/profiler/address/labels"
        print(f"\nPOST {url}")
        r = await client.post(url, json=payload, headers=headers)
        print("Status Code:", r.status_code)
        print("Response Body:", r.text)

        # Test Premium Label
        url = "https://api.nansen.ai/api/v1/profiler/address/premium-labels"
        print(f"\nPOST {url}")
        r = await client.post(url, json=payload, headers=headers)
        print("Status Code:", r.status_code)
        print("Response Body:", r.text)

if __name__ == "__main__":
    asyncio.run(test())
