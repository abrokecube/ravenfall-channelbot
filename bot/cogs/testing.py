from typing import Optional, Dict, Any
from ..commands import Context, Commands
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
        await ctx.reply(f"hi {ctx.msg.user.name}")

    @Cog.command(name="ping", help="Pong!")
    async def ping(self, ctx: Context):
        await ctx.reply("Pong!")

def setup(commands: Commands, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(TestingCog, **kwargs)