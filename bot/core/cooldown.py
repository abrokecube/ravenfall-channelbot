from __future__ import annotations
from typing import TYPE_CHECKING
from .enums import BucketType

if TYPE_CHECKING:
    from .events import BaseEvent

class Cooldown:
    def __init__(self, rate: int, per: float, bucket: BucketType | list[BucketType] = BucketType.USER):
        self.rate: int = rate
        self.per: float = per

        if not isinstance(bucket, list):
            bucket = [bucket]
        self.bucket: list[BucketType] = bucket
        self._windows: dict[str, list[float]] = {}
    
    def _get_bucket_key(self, event: BaseEvent) -> str:
        if hasattr(event, "get_bucket_key"):
            keys: list[str] = [str(event.get_bucket_key(t)) for t in self.bucket]
            return ":".join(keys)
        return ""

    def get_retry_after(self, event: BaseEvent) -> float:
        import time
        now = time.time()
        key = self._get_bucket_key(event)
        
        if key not in self._windows:
            return 0.0
            
        window = self._windows[key]
        # Remove expired timestamps
        window = [t for t in window if now - t < self.per]
        self._windows[key] = window
        
        if len(window) < self.rate:
            return 0.0
            
        return self.per - (now - window[0])

    def update_rate_limit(self, event: BaseEvent):
        import time
        now = time.time()
        key = self._get_bucket_key(event)
        
        if key not in self._windows:
            self._windows[key] = []
            
        window = self._windows[key]
        # Remove expired timestamps
        window = [t for t in window if now - t < self.per]
        window.append(now)
        self._windows[key] = window
