# ai/client.py
import json
import httpx
from typing import AsyncGenerator

OR_BASE = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 4000,
                 temperature: float = 0.1, json_mode: bool = True):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.json_mode = json_mode
        self.headers = {
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
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{OR_BASE}/chat/completions",
                headers=self.headers,
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
        """Non‑streaming. Returns dict if json_mode=True, else raw string."""
        payload = self._payload(messages, stream=False)
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{OR_BASE}/chat/completions",
                headers=self.headers,
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            if self.json_mode:
                return json.loads(content)
            return content
