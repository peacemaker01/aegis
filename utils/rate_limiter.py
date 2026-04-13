import asyncio, time

class RateLimiter:
    """Token-bucket rate limiter for Etherscan free tier (5 req/s)."""
    def __init__(self, calls_per_second: int = 4):
        self.interval = 1.0 / calls_per_second
        self._last   = 0.0
        self._lock   = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now  = time.monotonic()
            wait = self.interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

etherscan_limiter = RateLimiter(calls_per_second=4)
