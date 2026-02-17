"""Lightweight testing utilities and sample commands for development.

Contains simple ping/hi commands and test redeems used in development.
"""

from typing import Optional, Dict, Any
from ..commands.cog import Cog
from ..commands.events import CommandEvent
from ..commands.exceptions import CommandError
from ..commands.decorators import command

class TestingCog(Cog):
    """Small set of test commands and redeems for development.

    Includes basic chat commands and sample redeems used in CI/manual testing.
    """    
    @command(name="test", help="Test command")
    async def test(self, ctx: CommandEvent):
        await ctx.message.reply("Hello, world! Args: " + str(ctx.parsed_args.args))

    @command(name="test_error", help="Test error command")
    async def test_error(self, ctx: CommandEvent):
        raise Exception("Test error")

    @command(name="test_error_listener", help="Test error command")
    async def test_error_listener(self, ctx: CommandEvent):
        raise CommandError("Test error but cool")

    # @Cog.command(name="hi", help="Says hi")
    # async def hi(self, ctx: CommandEvent):
    #     """Greet the command author.

    #     Example:
    #         !hi
    #     """
    #     await ctx.reply(f"hi {ctx.author}")

    # @Cog.command(name="ping", help="Pong!")
    # async def ping(self, ctx: CommandEvent):
    #     """Simple ping command that replies with 'Pong!'."""
    #     await ctx.reply("Pong!")

    # @Cog.command()
    # async def roles(self, ctx: CommandEvent):
    #     """Show your current roles.
        
    #     Examples:
    #         !roles
    #     """
    #     role_names = [role.value for role in ctx.roles]
    #     await ctx.reply(f"Your roles: {', '.join(role_names)}")

    # @Cog.redeem(name="Test redeem")
    # async def test(self, ctx: TwitchRedeemCommandEvent):
    #     """A simple test redeem that notifies and fulfills the redemption."""
    #     await ctx.send("I am the almighty test redeem!")
    #     await ctx.update_status(CustomRewardRedemptionStatus.FULFILLED)

    # @Cog.redeem(name="Test error redeem")
    # async def test_error(self, ctx: TwitchRedeemCommandEvent):
    #     raise Exception("Test error")
    #     await ctx.send("You shouldnt be seeing this")
    #     await ctx.update_status(CustomRewardRedemptionStatus.FULFILLED)

