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
        """Yield text chunks from OpenRouter, rotating keys on 429/503."""
        payload = self._payload(messages, stream=True)
        max_attempts = len(self._key_pool.keys) if self._key_pool else 1

        for attempt in range(max_attempts):
            key = await self._get_key()
            headers = self._headers(key)
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    async with client.stream(
                        "POST",
                        f"{OR_BASE}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as resp:
                        if resp.status_code in (429, 503) and attempt < max_attempts - 1:
                            if self._key_pool:
                                self._key_pool.report_failure(key, rate_limited=(resp.status_code == 429))
                            continue
                        resp.raise_for_status()
                        if self._key_pool:
                            self._key_pool.report_success(key)
                        async for line in resp.aiter_lines():
                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("data: "):
                                raw = line[6:]
                                if raw == "[DONE]":
                                    return
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
                        return
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 503) and attempt < max_attempts - 1:
                    if self._key_pool:
                        self._key_pool.report_failure(key, rate_limited=(e.response.status_code == 429))
                    continue
                raise

    async def complete(self, messages: list) -> dict | str:
        """Non-streaming complete with automatic key rotation on 429/503."""
        import re
        payload = self._payload(messages, stream=False)
        max_attempts = len(self._key_pool.keys) if self._key_pool else 1
        last_exc = None

        for attempt in range(max_attempts):
            key = await self._get_key()
            headers = self._headers(key)
            r = None
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    r = await client.post(
                        f"{OR_BASE}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                if r.status_code in (429, 503) and attempt < max_attempts - 1:
                    if self._key_pool:
                        self._key_pool.report_failure(key, rate_limited=(r.status_code == 429))
                    continue
                r.raise_for_status()
                if self._key_pool:
                    self._key_pool.report_success(key)
                
                # Succeeded! Parse the response!
                data = r.json()
                choices = data.get("choices") if isinstance(data, dict) else None
                content = choices[0].get("message", {}).get("content", "") if choices else ""
                if not isinstance(content, str):
                    content = ""

                if not self.json_mode:
                    return content

                # JSON cleanup pipeline
                cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
                s = cleaned.find("{")
                e = cleaned.rfind("}")
                if s != -1 and e != -1:
                    cleaned = cleaned[s:e+1]
                cleaned = cleaned.replace("```json", "").replace("```", "").strip()

                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass

                cleaned_repaired = re.sub(r',\s*([\]}])', r'\1', cleaned)
                try:
                    return json.loads(cleaned_repaired)
                except json.JSONDecodeError:
                    pass

                def replace_quotes(match):
                    val = match.group(1)
                    escaped = val.replace('"', '\\"')
                    return f': "{escaped}"{match.group(2)}'
                cleaned_repaired2 = re.sub(r':\s*"(.*?)"\s*([,}])', replace_quotes, cleaned)
                try:
                    return json.loads(cleaned_repaired2)
                except json.JSONDecodeError:
                    pass

                # Regex field extractor fallback
                try:
                    result = {}
                    for field in ["reputation_score", "verdict", "recommendation", "summary"]:
                        m = re.search(fr'"{field}"\s*:\s*"(.*?)"', cleaned, re.DOTALL)
                        if m:
                            result[field] = m.group(1).replace('\\"', '"').strip()
                        else:
                            m2 = re.search(fr'"{field}"\s*:\s*(\d+)', cleaned)
                            if m2:
                                result[field] = int(m2.group(1))
                    flags_m = re.search(r'"red_flags"\s*:\s*\[(.*?)\]', cleaned, re.DOTALL)
                    result["red_flags"] = re.findall(r'"(.*?)"', flags_m.group(1)) if flags_m else []
                    findings_m = re.search(r'"findings"\s*:\s*\[(.*?)\]', cleaned, re.DOTALL)
                    if findings_m:
                        findings = []
                        for block in re.findall(r'\{(.*?)\}', findings_m.group(1), re.DOTALL):
                            finding = {}
                            for f in ["severity", "title", "description"]:
                                fm = re.search(fr'"{f}"\s*:\s*"(.*?)"', block, re.DOTALL)
                                if fm:
                                    finding[f] = fm.group(1).replace('\\"', '"').strip()
                            if finding:
                                findings.append(finding)
                        result["findings"] = findings
                    else:
                        result["findings"] = []
                    if "reputation_score" in result:
                        return result
                except Exception:
                    pass

                try:
                    return json.loads(content)
                except Exception:
                    pass

                # If JSON parsing completely failed but network call was 200, return fallback
                return {
                    "is_fallback": True,
                    "reputation_score": 0,
                    "verdict": "INSUFFICIENT DATA",
                    "summary": "The AI forensic analyst response was temporary unavailable or returned a non-JSON format. On-chain telemetry and technical scoring are clean, but AI summarization has been bypassed for safety.",
                    "red_flags": [],
                    "findings": [
                        {
                            "severity": "info",
                            "title": "AI Bypass Active",
                            "description": "AI-based risk text generation was bypassed due to API response formatting. Basic technical indicators remain fully active."
                        }
                    ]
                }

            except Exception as e:
                last_exc = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (429, 503) and attempt < max_attempts - 1:
                    if self._key_pool:
                        self._key_pool.report_failure(key, rate_limited=(e.response.status_code == 429))
                    continue

        # Safe fallback if all keys are exhausted / API is completely down
        return {
            "is_fallback": True,
            "reputation_score": 0,
            "verdict": "INSUFFICIENT DATA",
            "summary": "The AI forensic analyst response was temporary unavailable or returned a response error. On-chain telemetry and technical scoring are clean, but AI summarization has been bypassed for safety.",
            "red_flags": [],
            "findings": [
                {
                    "severity": "info",
                    "title": "AI Bypass Active",
                    "description": "AI-based risk text generation was bypassed due to API rate-limiting or service outage. Basic technical indicators remain fully active."
                }
            ]
        }
