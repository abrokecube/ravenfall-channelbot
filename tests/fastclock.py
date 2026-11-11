"""Speed up time for tests that run the real restart and update code.

Restart countdowns, restarts and updates wait in real seconds. `speed_up_time`
makes every `asyncio.sleep` shorter by `SPEEDUP` and makes `time.time()` and
`time.monotonic()` in the bot modules run `SPEEDUP` times faster, so the code
sees the same seconds pass while the test waits far less. The test's own
`asyncio.sleep` calls are sped up too, so a test can still sleep in game seconds.
"""

import asyncio
import time
import unittest
from unittest import mock

SPEEDUP = 20

MODULES_USING_TIME = (
    "bot.ravenfallchannel",
    "bot.ravenfallrestarttask",
    "bot.ravenfallupdater",
)


class FastTime:
    """Stands in for the `time` module: `time()` and `monotonic()` run fast."""

    def __init__(self, speedup: float):
        self._speedup = speedup
        self._real_start = time.monotonic()
        self._wall_start = time.time()

    def _elapsed(self) -> float:
        return (time.monotonic() - self._real_start) * self._speedup

    def time(self) -> float:
        return self._wall_start + self._elapsed()

    def monotonic(self) -> float:
        return self._real_start + self._elapsed()

    def __getattr__(self, name):
        return getattr(time, name)


def speed_up_time(test: unittest.TestCase, speedup: float = SPEEDUP) -> None:
    real_sleep = asyncio.sleep

    async def fast_sleep(delay, result=None):
        return await real_sleep(max(delay, 0) / speedup, result)

    fast_time = FastTime(speedup)
    patchers = [mock.patch("asyncio.sleep", fast_sleep)]
    patchers += [mock.patch(f"{module}.time", fast_time) for module in MODULES_USING_TIME]
    for patcher in patchers:
        patcher.start()
        test.addCleanup(patcher.stop)
