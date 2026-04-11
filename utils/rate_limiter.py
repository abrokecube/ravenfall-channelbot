"""Async rate limiter utility."""

import asyncio
import time
from collections import deque


class AsyncRateLimiter:
    """An asynchronous rate limiter using a sliding window.

    Attributes:
        rate (int): The maximum number of requests allowed.
        per (float): The time window in seconds.
    """

    def __init__(self, rate: int, per: float) -> None:
        """Initialize the rate limiter.

        Args:
            rate (int): The maximum number of operations allowed in the given timeframe.
            per (float): The timeframe in seconds.
        """
        self.rate: int = rate
        self.per: float = per
        self._history: dict[str | None, deque[float]] = {}
        self._locks: dict[str | None, asyncio.Lock] = {}

    def _get_lock(self, key: str | None) -> asyncio.Lock:
        """Get or create an asyncio lock for the given key.

        Args:
            key (Optional[str]): The key to get the lock for.

        Returns:
            asyncio.Lock: The lock for the specified key.
        """
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def acquire(self, key: str | None = None) -> None:
        """Wait until it is safe to proceed without exceeding the rate limit.

        Args:
            key (Optional[str], optional): The key to rate limit on. Defaults to None.
        """
        lock = self._get_lock(key)

        async with lock:
            if key not in self._history:
                self._history[key] = deque()

            history = self._history[key]

            while True:
                now = time.monotonic()

                # Remove timestamps older than the time window
                while history and history[0] <= now - self.per:
                    __ = history.popleft()

                if len(history) < self.rate:
                    history.append(time.monotonic())
                    return

                # We need to wait until the oldest timestamp expires
                time_to_wait = (history[0] + self.per) - now
                if time_to_wait > 0:
                    await asyncio.sleep(time_to_wait)

    def add(self, key: str | None = None) -> None:
        """Add an event to the rate limiter without waiting.

        This will record an event in the sliding window directly, regardless
        of whether the rate limit is currently exceeded.

        Args:
            key (str | None, optional): The key to add the event for. Defaults to None.
        """
        if key not in self._history:
            self._history[key] = deque()

        history = self._history[key]
        now = time.monotonic()

        # Clean up old timestamps
        while history and history[0] <= now - self.per:
            __ = history.popleft()

        history.append(now)
