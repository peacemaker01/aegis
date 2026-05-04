# utils/api_key_pool.py
"""
Thread-safe API key pool with round-robin selection and rate limit awareness.
"""
import asyncio
import time
from typing import List, Dict


class ApiKeyPool:
    def __init__(self, keys: List[str], calls_per_second: float = 5.0):
        if not keys:
            raise ValueError("At least one API key required")
        self.keys = keys
        self.calls_per_second = calls_per_second
        self._lock = asyncio.Lock()
        self._index = 0
        
        self._last_call: Dict[str, float] = {key: 0.0 for key in keys}
        self._failures: Dict[str, int] = {key: 0 for key in keys}
        self._disabled_until: Dict[str, float] = {key: 0.0 for key in keys}
    
    async def acquire(self) -> str:
        async with self._lock:
            while True:
                for _ in range(len(self.keys)):
                    key = self.keys[self._index]
                    self._index = (self._index + 1) % len(self.keys)
                    
                    now = time.monotonic()
                    if self._disabled_until.get(key, 0) > now:
                        continue
                    
                    last = self._last_call.get(key, 0)
                    interval = 1.0 / self.calls_per_second
                    wait = interval - (now - last)
                    
                    if wait <= 0:
                        self._last_call[key] = now
                        return key
                
                await asyncio.sleep(0.1)
    
    def report_success(self, key: str):
        self._failures[key] = 0
    
    def report_failure(self, key: str, rate_limited: bool = False):
        self._failures[key] = self._failures.get(key, 0) + 1
        if rate_limited or self._failures[key] >= 3:
            self._disabled_until[key] = time.monotonic() + 60
    
    def available_keys(self) -> int:
        now = time.monotonic()
        return sum(1 for key in self.keys if self._disabled_until.get(key, 0) <= now)