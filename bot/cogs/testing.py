from typing import Optional, Dict, Any
from ..commands import Context, Commands, TwitchRedeemContext, CustomRewardRedemptionStatus
from ..cog import Cog

class TestingCog(Cog):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    
    # @Cog.command(name="test", help="Test command")
    # async def test(self, ctx: Context):
    #     await ctx.reply("Hello, world! Args: " + str(ctx.args.args))

    # @Cog.command(name="test_error", help="Test error command")
    # async def test_error(self, ctx: Context):
    #     raise Exception("Test error")

    @Cog.command(name="hi", help="Says hi")
    async def hi(self, ctx: Context):
        await ctx.reply(f"hi {ctx.author}")

    @Cog.command(name="ping", help="Pong!")
    async def ping(self, ctx: Context):
        await ctx.reply("Pong!")

    @Cog.command()
    async def roles(self, ctx: Context):
        """Show your current roles.
        
        Examples:
            !roles
        """
        role_names = [role.value for role in ctx.roles]
        await ctx.reply(f"Your roles: {', '.join(role_names)}")

    @Cog.redeem(name="Test redeem")
    async def test(self, ctx: TwitchRedeemContext):
        await ctx.send("I am the almighty test redeem!")
        await ctx.update_status(CustomRewardRedemptionStatus.FULFILLED)

    @Cog.redeem(name="Test error redeem")
    async def test_error(self, ctx: TwitchRedeemContext):
        raise Exception("Test error")
        await ctx.send("You shouldnt be seeing this")
        await ctx.update_status(CustomRewardRedemptionStatus.FULFILLED)

def setup(commands: Commands, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(TestingCog, **kwargs)