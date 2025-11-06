from typing import Optional, Dict, Any
from ..commands import CommandContext, Commands, RedeemContext, CustomRewardRedemptionStatus
from ..cog import Cog

class RedeemCog(Cog):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @Cog.command(name="createreward", help="Create a custom channel point reward")
    async def createreward(self, ctx: CommandContext):
        title = "New reward"
        if ctx.args.grouped_args:
            title = ctx.args.grouped_args[0]
        cost_text = ctx.args.get_flag(['c', 'cost'], default="1000").value
        if cost_text and cost_text.isnumeric():
            cost = int(cost_text)
        else:
            cost = 1000
        
        await ctx.twitch.create_custom_reward(
            ctx.msg.room.room_id,
            title,
            cost = cost,
            prompt = ctx.args.get_flag(['p', 'prompt'], default=None).value,
            is_enabled = ctx.args.get_flag(['e', 'enabled'], default=True).value is not None,
        )
        await ctx.reply("Created reward")
    
