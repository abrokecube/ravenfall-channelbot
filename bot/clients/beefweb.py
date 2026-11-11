"""Client for beefweb, the foobar2000 web API plugin.

The server is set by BEEFWEB_URL. The API is described in beefweb's
docs/player-api.yml (https://github.com/hyperblast/beefweb).
"""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from ..config import ConfigError, get_settings
from .session import get_session

# foobar2000 title formatting, one per column of the active item.
_COLUMNS = ("%title%", "%artist%", "%album%")
_TIMEOUT = aiohttp.ClientTimeout(total=5)


@dataclass(frozen=True)
class NowPlaying:
    state: str
    """`playing`, `paused` or `stopped`."""
    title: str = ""
    artist: str = ""
    album: str = ""
    position: float = 0.0
    duration: float = 0.0
    """Seconds. 0 or less when unknown, as for a stream."""


async def get_now_playing() -> NowPlaying:
    """Asks foobar2000 what it is playing. Raises ConfigError when BEEFWEB_URL isn't set."""
    url = get_settings().beefweb_url
    if not url:
        raise ConfigError("BEEFWEB_URL must be set")
    params = {"columns": ",".join(_COLUMNS)}
    async with get_session().get(f"{url}/api/player", params=params, timeout=_TIMEOUT) as response:
        response.raise_for_status()
        data = await response.json()
    player = data["player"]
    state = player.get("playbackState", "stopped")
    item = player.get("activeItem") or {}
    columns = item.get("columns") or []
    if state == "stopped" or item.get("index", -1) < 0 or len(columns) < len(_COLUMNS):
        return NowPlaying(state="stopped")
    title, artist, album = columns[: len(_COLUMNS)]
    return NowPlaying(
        state=state,
        title=title,
        artist=artist,
        album=album,
        position=float(item.get("position") or 0),
        duration=float(item.get("duration") or 0),
    )
