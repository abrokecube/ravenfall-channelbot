from __future__ import annotations

from bot.core.components import Cog
from bot.integrations.chat_messages import UserRole, checks
from bot.integrations.chat_messages.utils import min_permission_level
from bot.integrations.commands import (
    CommandError,
    CommandEvent,
    MinPermissionLevel,
    command,
    parameter,
)
from bot.integrations.twitch import TwitchChannel
from bot.integrations.twitch.converters import TwitchInstanceConverter


class TwitchCog(Cog):
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @parameter(
        "channel",
        converter=TwitchInstanceConverter,
        default=TwitchInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command()
    async def createreward(
        self,
        ctx: CommandEvent,
        title: str = "New reward",
        cost: int = 1000,
        prompt: str | None = None,
        *,
        channel: TwitchChannel,
    ):
        """Create a custom channel point reward."""
        if channel.channel_id != ctx.message.room_id and not min_permission_level(
            ctx.message, UserRole.BOT_ADMINISTRATOR
        ):
            msg = "You do not have permission to specify a channel."
            raise CommandError(msg)
        __ = await channel.create_custom_reward(title, cost, prompt)
        await ctx.message.reply("Created reward")
