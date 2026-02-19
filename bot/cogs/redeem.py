"""Simple redeem utilities and helpers.

Small utilities for creating channel point rewards.
"""

from typing import Optional, Dict, Any
from ..commands.cog import Cog
from ..commands.decorators import command, checks
from ..commands.enums import UserRole
from ..commands.events import CommandEvent, TwitchMessageEvent
from ..commands.checks import TwitchOnly, MinPermissionLevel
# from ..commands import Context, Commands, TwitchRedeemContext, TwitchContext, checks, parameter
# from ..command_enums import UserRole, Platform
# from ..command_utils import HasRole, TwitchOnly
# from ..commands_old.cog import Cog

class RedeemCog(Cog):
    """Cog exposing simple reward management commands for Twitch channels."""
    @command(name="createreward", help="Create a custom channel point reward")
    @checks(TwitchOnly, MinPermissionLevel(UserRole.MODERATOR))
    async def createreward(self, ctx: CommandEvent, title: str = "New reward", cost: int = 1000, prompt: str = None):
        """Create a custom channel point reward.

        Args:
            title: Reward title.
            cost: Cost in channel points.
            prompt: Optional prompt/description.
        """
        m: TwitchMessageEvent = ctx.message
        await m.channel_twitch.create_custom_reward(
            ctx.message.room_id,
            title,
            cost = cost,
            prompt = prompt
        )
        await ctx.message.reply("Created reward")
    
