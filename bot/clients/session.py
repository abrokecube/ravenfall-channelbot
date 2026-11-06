"""The one aiohttp session behind the bot's HTTP clients.

It is created on first use and `close_session()` ends it at shutdown.

Each request still gets its own connection (`force_close`). The game's query server
hasn't been tested with reused connections, and a failed query can restart a town.
There is no connection limit, because the middleman must not be throttled and
long waits on it or on WebOps must not hold up other requests. Cookies were never
kept between calls, so they aren't kept here either.

The default timeout is aiohttp's. Pass `timeout=` on a request to change it.
"""

from __future__ import annotations

import asyncio

import aiohttp

_session: aiohttp.ClientSession | None = None
_session_loop: asyncio.AbstractEventLoop | None = None


def get_session() -> aiohttp.ClientSession:
    """Returns the shared session. Must be called from a running event loop."""
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is not None and not _session.closed and _session_loop is loop:
        return _session
    if _session is not None and not _session.closed:
        # Left by another event loop, which can't close it any more
        _session.detach()
    _session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=0, force_close=True),
        cookie_jar=aiohttp.DummyCookieJar(),
    )
    _session_loop = loop
    return _session


async def close_session() -> None:
    global _session, _session_loop
    session, _session, _session_loop = _session, None, None
    if session is not None:
        await session.close()
