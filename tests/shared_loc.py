"""One RavenfallLocalization for all tests.

Loading `data/definitions.yaml` takes about half a second, and the tests only
read from it, so they share one copy instead of loading it per test.
"""

from functools import cache

from bot.ravenfallloc import RavenfallLocalization


@cache
def shared_localization() -> RavenfallLocalization:
    return RavenfallLocalization("data/definitions.yaml")
