from __future__ import annotations

from ..commands.cog import Cog
from ..commands.decorators import command, parameter, checks
from ..commands.checks import MinPermissionLevel
from ..commands.converters import RFChannelConverter
from ..commands.enums import UserRole
from ..commands.events import CommandEvent

from ..ravenfallchannel import RFChannel
from ..ravenfallmanager import RFChannelManager


class BotStuffCog(Cog):
    def __init__(self, event_manager, watcher_urls=["http://127.0.0.1:8110"]):
        super().__init__(event_manager)

    @command(name="reload_strings", aliases=["reloadstrings"])
    @parameter("all_", display_name="all", aliases=["a"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def reload_strings(
        self, ctx: CommandEvent, *, all_: bool = False, channel: RFChannel = "this"
    ):
        """Reloads Ravenfall bot translation strings.

        Args:
            all: Reloads strings for all channels.
            channel: Channel to reload strings for.
        """
        rf_manager = self.global_context.require_service(RFChannelManager)
        if all_:
            for _channel in rf_manager.channels:
                _channel.rfloc.load_definitions()
                _channel.rfloc.load_translations()
            await ctx.message.reply("Strings reloaded for all channels!")
            return

        channel.rfloc.load_definitions()
        channel.rfloc.load_translations()
        await ctx.message.reply("Strings reloaded!")

    @command(name="pausemon", aliases=["pausemonitoring", "pause_monitoring"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def pause_monitoring(self, ctx: CommandEvent, *, channel: RFChannel = "this"):
        """Pause channel monitoring

        Args:
            channel: Channel to pause monitoring for.
        """
        if channel.monitoring_paused:
            await ctx.message.reply("Channel monitoring is already paused.")
            return
        channel.monitoring_paused = True
        await channel.stop()
        await ctx.message.reply("Channel monitoring paused.")

    @command(name="resumemon", aliases=["resumemonitoring", "resume_monitoring"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def resume_monitoring(self, ctx: CommandEvent, *, channel: RFChannel = "this"):
        """Resume channel monitoring

        Args:
            channel: Channel to resume monitoring for.
        """
        if not channel.monitoring_paused:
            await ctx.message.reply("Channel monitoring is not paused.")
            return
        channel.monitoring_paused = False
        await channel.start()
        await ctx.message.reply("Channel monitoring resumed.")
