from typing import Optional, Dict, Any
from ..commands import CommandContext, Commands, RedeemContext, CustomRewardRedemptionStatus
from ..cog import Cog

class RedeemCog(Cog):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @Cog.command(name="createreward", help="Create a custom channel point reward")
    async def createreward(self, ctx: CommandContext):
        if not (ctx.msg.user.mod or ctx.msg.room.room_id == ctx.msg.user.id):
            return
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
            prompt = ctx.args.get_flag(['p', 'prompt'], default=None).value
        )
        await ctx.reply("Created reward")
    
