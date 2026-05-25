from __future__ import annotations

from bot.core.components import Cog
from bot.db.session import get_async_session
from bot.integrations.chat_messages import UserRole, checks
from bot.integrations.chat_messages.utils import min_permission_level
from bot.integrations.commands import (
    CommandError,
    CommandEvent,
    MinPermissionLevel,
    command,
    parameter,
)
from bot.integrations.twitch import (
    TwitchChannel,
    TwitchEvent,
    TwitchOnly,
    TwitchRedemptionEvent,
)
from bot.integrations.twitch.converters import TwitchInstanceConverter
from bot.services.event_waiter import EventWaiterService


class TwitchCog(Cog):
    @checks(MinPermissionLevel(UserRole.MODERATOR), TwitchOnly())
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
        internal_key: str | None = None,
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
        async with get_async_session() as session:
            __ = await channel.create_custom_reward(
                title, cost, prompt, internal_key=internal_key, db_session=session
            )
        await ctx.message.reply("Created reward")

    @checks(MinPermissionLevel(UserRole.MODERATOR), TwitchOnly())
    @command()
    async def setrewardkey(self, ctx: CommandEvent, key: str):
        """Set internal key of a custom reward."""
        if not isinstance(ctx.message, TwitchEvent):
            return
        event_waiter_srv = self.global_context.require_service(EventWaiterService)
        await ctx.reply("Use a channel points reward now!")
        try:
            matched_event = await event_waiter_srv.wait_for(
                TwitchRedemptionEvent,
                predicate=lambda x: x.author_id == ctx.message.author_id,
                timeout=30,
            )
        except TimeoutError as e:
            raise CommandError("Timed out.") from e
        async with get_async_session() as session:
            await matched_event.save_internal_key(key, session)
        await ctx.reply(f"Saved key for reward id {matched_event.id[:8]}...")
