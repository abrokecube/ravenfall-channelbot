"""
A cooldown system that tracks and enforces cooldowns for arbitrary keys with bucket support.
"""
from typing import Dict, Optional, Tuple
import time
import asyncio
from dataclasses import dataclass
from contextlib import asynccontextmanager
import logging

# Configure logger for this module
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CooldownBucket:
    """Represents a cooldown bucket with rate limit and time window."""
    rate: int
    per: float


@dataclass
class _CooldownState:
    """Internal state for a cooldown."""
    reset_time: float
    count: int


class CooldownScoped:
    """A scoped cooldown object for a specific key and bucket."""

    def __init__(self, cooldown: 'Cooldown', key: str, bucket: CooldownBucket):
        self._cooldown = cooldown
        self.key = key
        self.bucket = bucket

    def is_rate_limited(self) -> bool:
        """Check if the scoped key is currently rate limited."""
        return self._cooldown.is_rate_limited(self.key, self.bucket)

    def update_rate_limit(self) -> Tuple[bool, float]:
        """Update the rate limit for the scoped key."""
        return self._cooldown.update_rate_limit(self.key, self.bucket)

    async def acquire(self) -> bool:
        """Thread-safe version of update_rate_limit that can be used with async/await."""
        return await self._cooldown.acquire(self.key, self.bucket)

    def get_retry_after(self) -> float:
        """Get the time in seconds until the cooldown expires."""
        return self._cooldown.get_retry_after(self.key, self.bucket)

    def clear(self):
        """Clear the cooldown for this scoped key."""
        self._cooldown.clear(self.key, self.bucket)

    @asynccontextmanager
    async def __call__(self):
        """Async context manager that acquires the cooldown and yields True if acquired."""
        acquired = await self.acquire()
        yield acquired


class Cooldown:
    """
    A cooldown system that tracks and enforces cooldowns for arbitrary keys.

    Example:
        cooldown = Cooldown()
        bucket = CooldownBucket(rate=3, per=60.0)  # 3 uses per 60 seconds

        # Basic usage
        if cooldown.is_rate_limited("user123", bucket):
            logger.debug("Action is rate limited")
        else:
            logger.debug("Action allowed")
            cooldown.update_rate_limit("user123", bucket)

        # Using scoped cooldown
        user_cd = cooldown.scoped("user123", bucket)
        if not user_cd.is_rate_limited():
            user_cd.update_rate_limit()
            logger.debug("Action allowed")

        # Using async context manager
        user_cd = cooldown.scoped("user123", bucket)
        async with user_cd() as allowed:
            if allowed:
                logger.debug("Action allowed in context")
    """

    def __init__(self):
        self._cooldowns: Dict[Tuple[str, CooldownBucket], _CooldownState] = {}
        self._lock = asyncio.Lock()

    def _get_state(self, key: str, bucket: CooldownBucket) -> Optional[_CooldownState]:
        """Get the current state for a key and bucket."""
        state = self._cooldowns.get((key, bucket))
        if state and time.time() > state.reset_time:
            del self._cooldowns[(key, bucket)]
            return None
        return state

    def is_rate_limited(self, key: str, bucket: CooldownBucket) -> bool:
        """
        Check if the given key is currently rate limited for the specified bucket.
        """
        state = self._get_state(key, bucket)
        return state is not None and state.count >= bucket.rate

    def update_rate_limit(self, key: str, bucket: CooldownBucket) -> Tuple[bool, float]:
        """
        Update the rate limit for the given key and bucket.
        Returns a tuple of (is_rate_limited, retry_after).
        """
        current_time = time.time()
        state = self._get_state(key, bucket)

        if state is None:
            # Start a new cooldown window
            new_state = _CooldownState(reset_time=current_time + bucket.per, count=1)
            self._cooldowns[(key, bucket)] = new_state
            return False, 0.0

        # Update existing cooldown
        state.count += 1
        if state.count > bucket.rate:
            retry_after = max(0.0, state.reset_time - current_time)
            return True, retry_after

        return False, 0.0

    async def acquire(self, key: str, bucket: CooldownBucket) -> bool:
        """
        Thread-safe version of update_rate_limit. Returns True if allowed, False if limited.
        """
        async with self._lock:
            is_limited, _ = self.update_rate_limit(key, bucket)
            return not is_limited

    def get_retry_after(self, key: str, bucket: CooldownBucket) -> float:
        """
        Get the time in seconds until the cooldown expires for a key.
        Returns 0.0 if not on cooldown.
        """
        state = self._get_state(key, bucket)
        if state is None or state.count <= bucket.rate:
            return 0.0
        return max(0.0, state.reset_time - time.time())

    def scoped(self, key: str, bucket: CooldownBucket) -> CooldownScoped:
        """
        Create a scoped cooldown object for the given key and bucket.
        """
        return CooldownScoped(self, key, bucket)

    def clear(self, key: Optional[str] = None, bucket: Optional[CooldownBucket] = None):
        """
        Clear cooldown entries.

        Args:
            key: If provided, only clear entries for this key.
            bucket: If provided, only clear entries for this bucket.
        """
        if key is None and bucket is None:
            self._cooldowns.clear()
            return

        keys_to_remove = [
            k for k in self._cooldowns
            if (key is None or k[0] == key) and (bucket is None or k[1] == bucket)
        ]

        for k in keys_to_remove:
            del self._cooldowns[k]
