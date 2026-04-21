"""Lightweight testing utilities and sample commands for development.

Contains simple ping/hi commands and test redeems used in development.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.core.components import Cog
from bot.integrations.chat_messages.deco import checks
from bot.integrations.chat_messages.enums import UserRole
from bot.integrations.commands.checks import MinPermissionLevel
from bot.integrations.commands.deco import command
from bot.integrations.commands.events import CommandEvent  # noqa: TC001
from bot.integrations.commands.exceptions import CommandError
from bot.integrations.twitch.deco import on_twitch_redeem

if TYPE_CHECKING:
    from bot.integrations.twitch.events import TwitchRedemptionEvent


class TestingCog(Cog):
    """Small set of test commands and redeems for development.

    Includes basic chat commands and sample redeems used in CI/manual testing.
    """

    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command(name="test")
    async def test(self, ctx: CommandEvent):
        """Test command."""
        await ctx.message.reply("Hello, world! Args: " + str(ctx.parsed_args.args))

    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command(name="test_error", help_text="Test error command")
    async def test_error(self, ctx: CommandEvent):
        """Test error command."""
        msg = "Test error"
        raise Exception(msg)

    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command(name="test_error_listener", help_text="Test error command")
    async def test_error_listener(self, ctx: CommandEvent):
        """Test error command."""
        msg = "Test error but cool"
        raise CommandError(msg)

    @on_twitch_redeem(lambda e: e.redeem_name.lower() == "test redeem")
    async def test_redeem(self, ctx: TwitchRedemptionEvent, match_result: bool):
        """Test redeem."""
        await ctx.reply(f"Test redeem text: {ctx.text}")

    @on_twitch_redeem(lambda e: e.redeem_name.lower() == "test error redeem")
    async def test_error_redeem(self, ctx: TwitchRedemptionEvent, match_result: bool):
        """Test error redeem."""
        msg = "boom i exploded"
        raise CommandError(msg)

    @command(name="ping", help_text="Pong!")
    async def ping(self, ctx: CommandEvent):
        """Command that replies with 'Pong!'."""
        await ctx.message.reply("Pong!")

    # @on_message(lambda e: re.match(r"^\?ping", e.text, re.IGNORECASE))
    # async def ping_alias(self, ctx: MessageEvent, result: re.Match):
    #     await self.event_manager.execute_text("ping", ctx)

    # @on_message(lambda e: bool(re.match(r"^\?\?(ping)", e.text, re.IGNORECASE)))
    # async def ping_alias(self, ctx: MessageEvent, result: re.Match):
    #     await self.event_manager.execute_text(ctx.text[2:], ctx)

    # @on_message(lambda e: bool(re.match(r"^\?\?(error)", e.text, re.IGNORECASE)))
    # async def test_error_alias(self, ctx: MessageEvent, result: re.Match):
    #     responses = await self.event_manager.execute_text("test_error", ctx)
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
