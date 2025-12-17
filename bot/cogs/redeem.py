"""Simple redeem utilities and helpers.

Small utilities for creating channel point rewards.
"""

from typing import Optional, Dict, Any
from ..commands import Context, Commands, TwitchRedeemContext, TwitchContext, checks, parameter
from ..command_enums import UserRole, Platform
from ..command_utils import HasRole, TwitchOnly
from ..cog import Cog

class RedeemCog(Cog):
    """Cog exposing simple reward management commands for Twitch channels."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @Cog.command(name="createreward", help="Create a custom channel point reward")
    @checks(TwitchOnly, HasRole(UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR))
    async def createreward(self, ctx: TwitchContext, title: str = "New reward", cost: int = 1000, prompt: str = None):
        """Create a custom channel point reward.

        Args:
            title: Reward title.
            cost: Cost in channel points.
            prompt: Optional prompt/description.
        """
        await ctx.api.create_custom_reward(
            ctx.data.room.room_id,
            title,
            cost = cost,
            prompt = prompt
        )
        await ctx.reply("Created reward")
    
