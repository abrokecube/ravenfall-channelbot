"""Music cog: what foobar2000 is playing, through beefweb."""

from __future__ import annotations

import logging

from ..clients.beefweb import NowPlaying, get_now_playing
from ..commands.cog import Cog
from ..commands.decorators import command, cooldown
from ..commands.enums import BucketType
from ..commands.events import CommandEvent
from ..config import ConfigError

logger = logging.getLogger(__name__)


def format_time(seconds: float) -> str:
    """`m:ss`, or `h:mm:ss` from an hour up."""
    seconds = max(int(seconds), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes}:{seconds:02}"


def format_now_playing(np: NowPlaying) -> str:
    if np.state == "stopped":
        return "Nothing is playing right now."
    song = np.title or "Unknown title"
    if np.artist:
        song += f" by {np.artist}"
    if np.album:
        song += f" from {np.album}"
    position = format_time(np.position)
    if np.duration > 0:
        position += f" / {format_time(np.duration)}"
    paused = " (paused)" if np.state == "paused" else ""
    return f"Now playing: {song} [{position}]{paused}"


class MusicCog(Cog):
    @command(name="nowplaying", aliases=["np", "song"])
    @cooldown(1, 5, BucketType.CHANNEL)
    async def now_playing(self, ctx: CommandEvent):
        """Shows the song that is playing, with its artist, album and position."""
        try:
            np = await get_now_playing()
        except ConfigError:
            await ctx.message.reply("Now playing isn't set up.")
            return
        except Exception:
            logger.warning("Couldn't get the song from beefweb", exc_info=True)
            await ctx.message.reply("Couldn't get the song right now.")
            return
        await ctx.message.reply(format_now_playing(np))
