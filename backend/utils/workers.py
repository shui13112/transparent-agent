import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from utils.rate_limiter import get_global_rate_limiter


class WorkerPool:
    def __init__(self, max_workers: int, rate_limit_delay: float = 0.0, request_delay: float = 0.0):
        """
        Initialize WorkerPool with concurrency and rate limiting.

        Args:
            max_workers: Maximum number of concurrent workers
            rate_limit_delay: Minimum seconds between requests GLOBALLY (0 = no limit)
                             This delay is enforced across ALL WorkerPools to prevent
                             overwhelming rate-limited APIs.
            request_delay: Fixed sleep after each request, with ±30% random jitter.
                           Simulates human reading time between page navigations.
                           Example: 0.5 for 0.5~0.65s delay per request.
        """
        self.max_workers = max_workers
        self.rate_limit_delay = rate_limit_delay
        self.request_delay = request_delay
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.semaphore = asyncio.Semaphore(max_workers)

        # Configure the global rate limiter
        # All WorkerPools share the same rate limiter instance
        global_limiter = get_global_rate_limiter()
        global_limiter.configure(rate_limit_delay)

    @asynccontextmanager
    async def throttle(self):
        """
        Throttle requests with three-layer control:

        1. Semaphore — concurrency cap (how many at once)
        2. Global rate limiter — cross-pool minimum interval (with jitter)
        3. Per-request sleep — fixed delay with jitter, simulates human pacing
        """
        async with self.semaphore:
            global_limiter = get_global_rate_limiter()
            await global_limiter.wait_if_needed()
            yield
            if self.request_delay > 0:
                await asyncio.sleep(self.request_delay)
