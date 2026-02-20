"""Lightweight testing utilities and sample commands for development.

Contains simple ping/hi commands and test redeems used in development.
"""

from typing import Optional, Dict, Any
from ..commands.cog import Cog
from ..commands.events import CommandEvent, TwitchRedemptionEvent, MessageEvent
from ..commands.exceptions import CommandError
from ..commands.decorators import command, on_match, on_twitch_redeem, checks, on_message
from ..commands.checks import MinPermissionLevel
from ..commands.enums import UserRole

import re

class TestingCog(Cog):
    """Small set of test commands and redeems for development.

    Includes basic chat commands and sample redeems used in CI/manual testing.
    """    
    @command(name="test", help="Test command")
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def test(self, ctx: CommandEvent):
        await ctx.message.reply("Hello, world! Args: " + str(ctx.parsed_args.args))

    @command(name="test_error", help="Test error command")
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def test_error(self, ctx: CommandEvent):
        raise Exception("Test error")

    @command(name="test_error_listener", help="Test error command")
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def test_error_listener(self, ctx: CommandEvent):
        raise CommandError("Test error but cool")
    
    @on_twitch_redeem(lambda e: e.redeem_name.lower() == "test redeem")
    async def test_redeem(self, ctx: TwitchRedemptionEvent, match_result: bool):
        await ctx.reply(f"Test redeem text: {ctx.text}")

    @on_twitch_redeem(lambda e: e.redeem_name.lower() == "test error redeem")
    async def test_error_redeem(self, ctx: TwitchRedemptionEvent, match_result: bool):
        raise CommandError("boom i exploded")

    @command(name="ping", help="Pong!")
    async def ping(self, ctx: CommandEvent):
        """Simple ping command that replies with 'Pong!'."""
        await ctx.message.reply("Pong!")
        
    # @on_message(lambda e: bool(re.match(r"^\?\?(ping)", e.text, re.IGNORECASE)))
    # async def ping_alias(self, ctx: MessageEvent, result: re.Match):
    #     await self.event_manager.execute_text(ctx.text[2:], ctx)

    # @on_message(lambda e: bool(re.match(r"^\?\?(error)", e.text, re.IGNORECASE)))
    # async def test_error_alias(self, ctx: MessageEvent, result: re.Match):
    #     responses = await self.event_manager.execute_text("test_error", ctx, capture_responses=True)
    #     print(responses)

    @command()
    async def roles(self, ctx: CommandEvent):
        """Show your current roles.
        
        Examples:
            !roles
        """
        role_names = [role.value for role in ctx.message.author_roles]
        await ctx.message.reply(f"Your roles: {', '.join(role_names)}")

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

