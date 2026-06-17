"""
Global rate limiter for scraper requests.

Ensures that SCRAPER_RATE_LIMIT_DELAY is enforced globally across ALL WorkerPools,
not just per-pool. This prevents multiple concurrent researchers from overwhelming
rate-limited APIs like Firecrawl.
"""
import asyncio
import random
import threading
import time
from typing import ClassVar


class GlobalRateLimiter:
    """
    Singleton global rate limiter.

    Ensures minimum delay between ANY scraper requests across the entire application,
    regardless of how many WorkerPools or GPTResearcher instances are active.
    """

    _instance: ClassVar['GlobalRateLimiter'] = None
    _async_lock: ClassVar[asyncio.Lock] = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize the global rate limiter (only once)."""
        if self._initialized:
            return

        self.last_request_time = 0.0
        self.rate_limit_delay = 0.0
        self._initialized = True

    @classmethod
    def get_lock(cls):
        """Get or create the async lock (must be called from async context)."""
        if cls._async_lock is None:
            cls._async_lock = asyncio.Lock()
        return cls._async_lock

    def configure(self, rate_limit_delay: float):
        """
        Configure the global rate limit delay.

        Args:
            rate_limit_delay: Minimum seconds between requests (0 = no limit)
        """
        self.rate_limit_delay = rate_limit_delay

    async def wait_if_needed(self):
        """
        Wait if needed to enforce global rate limiting.

        Each request atomically reserves a future time slot; the sleep happens
        *outside* the lock so that other coroutines can reserve their own
        slots concurrently.  This preserves the Semaphore-based concurrency
        while still enforcing a minimum interval between successive requests.
        """
        if self.rate_limit_delay <= 0:
            return  # No rate limiting

        lock = self.get_lock()
        async with lock:
            now = time.time()
            # Reserve the next available slot
            scheduled = max(self.last_request_time, now) + self.rate_limit_delay
            self.last_request_time = scheduled

        # Sleep outside the lock so peers can grab their own slots
        delay = scheduled - now
        if delay > 0:
            jitter = random.uniform(0, self.rate_limit_delay * 0.3)
            await asyncio.sleep(delay + jitter)

    def reset(self):
        """Reset the rate limiter state (useful for testing)."""
        self.last_request_time = 0.0


# Singleton instance
_global_rate_limiter = GlobalRateLimiter()


def get_global_rate_limiter() -> GlobalRateLimiter:
    """Get the global rate limiter singleton instance."""
    return _global_rate_limiter
