"""The aiohttp sessions behind the bot's HTTP clients.

There is one session per event loop, because a session can only be used on the
loop it was made on. The bot has several: twitchAPI runs chat and each EventSub
websocket on its own thread and loop, and commands run on those loops. Sessions
are created on first use and `close_session()` ends them at shutdown.

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

_sessions: dict[asyncio.AbstractEventLoop, aiohttp.ClientSession] = {}


def get_session() -> aiohttp.ClientSession:
    """Returns this event loop's session. Must be called from a running event loop."""
    loop = asyncio.get_running_loop()
    session = _sessions.get(loop)
    if session is not None and not session.closed:
        return session
    for other_loop in list(_sessions):
        if other_loop.is_closed():
            _sessions.pop(other_loop).detach()
    session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=0, force_close=True),
        cookie_jar=aiohttp.DummyCookieJar(),
    )
    _sessions[loop] = session
    return session


async def close_session() -> None:
    """Ends every session. Call it once the other loops have stopped.

    A session can't be closed from another loop, so theirs are only detached.
    """
    loop = asyncio.get_running_loop()
    sessions = list(_sessions.items())
    _sessions.clear()
    for session_loop, session in sessions:
        if session_loop is loop:
            await session.close()
        else:
            session.detach()
