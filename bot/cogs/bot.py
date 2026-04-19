from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from bot.core.components import Cog
from bot.integrations.chat_messages.deco import checks
from bot.integrations.chat_messages.enums import UserRole
from bot.integrations.commands.checks import MinPermissionLevel
from bot.integrations.commands.deco import (
    command,
)
from bot.integrations.commands.events import CommandEvent  # noqa: TC001
from bot.integrations.commands.exceptions import CommandError
from bot.services.config_service import ConfigService

if TYPE_CHECKING:
    from bot.core.components import EventManager


class BotStuffCog(Cog):
    """Bot stuff cog."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)

    @command(name="sourcecode", aliases=["github", "source"])
    async def github_link(self, ctx: CommandEvent):
        """https://github.com/abrokecube/ravenfall-channelbot."""
        await ctx.reply(
            "Source code on GitHub: https://github.com/abrokecube/ravenfall-channelbot"
        )

    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command()
    async def reload_config(self, ctx: CommandEvent):
        """Reloads the bot's configuration."""
        config = self.global_context.get_service(ConfigService)
        if config is None:
            msg = "Config service not found"
            raise CommandError(msg)
        await asyncio.to_thread(config.reload)
        await ctx.reply("Config reloaded!")
