# scratch/test_ai_json_fallback.py
import asyncio
from ai.client import OpenRouterClient

class MockResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
    def json(self):
        return self._data
    def raise_for_status(self):
        pass

async def main():
    # Construct a client
    client = OpenRouterClient(api_key="mock", model="mock", json_mode=True)
    
    # We will mock the client's internal request method or mock complete behavior
    # Let's mock client._headers and POST request
    import httpx
    
    # Let's directly call the parser logic by mocking the response choice
    data = {
        "choices": [
            {
                "message": {
                    "content": "This is raw non-JSON text that will fail all json.loads and regex parsing."
                }
            }
        ]
    }
    
    # We will test the cleanup pipeline on this raw content!
    # To do this, let's execute the complete method or simulate it
    # We can mock httpx.AsyncClient.post to return MockResponse
    original_post = httpx.AsyncClient.post
    
    async def mock_post(*args, **kwargs):
        return MockResponse(200, data)
        
    httpx.AsyncClient.post = mock_post
    
    try:
        res = await client.complete([])
        print("Fallback test passed!")
        print("Parsed JSON Result:", res)
    except Exception as e:
        print("Failed with exception:", e)
    finally:
        httpx.AsyncClient.post = original_post

asyncio.run(main())
