# ai/client.py
import json
import httpx
from typing import AsyncGenerator, Optional, List
from utils.api_key_pool import ApiKeyPool

OR_BASE = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 4000,
        temperature: float = 0.0,
        json_mode: bool = True,
        api_keys: Optional[List[str]] = None,
        timeout: int = 120,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.json_mode = json_mode
        self.timeout = timeout

        if api_keys and len(api_keys) > 1:
            self._key_pool = ApiKeyPool(api_keys, calls_per_second=3.0)
            self._single_key = None
        else:
            self._key_pool = None
            self._single_key = api_keys[0] if api_keys else api_key

    async def _get_key(self) -> str:
        if self._key_pool:
            return await self._key_pool.acquire()
        return self._single_key

    def _headers(self, api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://aegis.app",
            "X-Title": "Aegis",
        }

    def _payload(self, messages: list, stream: bool) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def stream_audit(self, messages: list) -> AsyncGenerator[str, None]:
        """Yield text chunks as they stream from OpenRouter."""
        payload = self._payload(messages, stream=True)
        key = await self._get_key()
        headers = self._headers(key)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{OR_BASE}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        raw = line[6:]
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                            content = (
                                chunk["choices"][0]
                                .get("delta", {})
                                .get("content", "")
                            )
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue

    async def complete(self, messages: list) -> dict | str:
        """Non-streaming. Returns dict if json_mode=True, else raw string."""
        payload = self._payload(messages, stream=False)
        key = await self._get_key()
        headers = self._headers(key)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{OR_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            if self.json_mode:
                return json.loads(content)
            return content
