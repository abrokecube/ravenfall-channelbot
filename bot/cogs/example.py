"""Example Cog.

This cog demonstrates the new command system features:
- Typed arguments with automatic parsing
- Custom type converters
- Custom checks using @checks(check1, check2, ...)
- Google-style docstrings for documentation
- Remote callable methods for inter-bot communication
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, ClassVar, override

from msgspec import Struct

from bot.core.components import Cog
from bot.core.decorators import cooldown
from bot.core.enums import BucketType
from bot.integrations.chat_messages.deco import checks
from bot.integrations.chat_messages.enums import UserRole
from bot.integrations.commands.checks import HasRole, MinPermissionLevel
from bot.integrations.commands.converters import BaseConverter
from bot.integrations.commands.deco import (
    command,
    parameter,
    verification,
)
from bot.integrations.commands.events import CommandEvent
from bot.integrations.commands.exceptions import ArgumentConversionError
from bot.mixins.fastapi_routes import FastAPIRoutesMixin, api_route
from bot.mixins.remote_callable import RemoteCallableMixin, remote_callable
from bot.services.web_service import APIServer

if TYPE_CHECKING:
    from bot.core.components import GlobalContext


# Example custom type with converter
class Color(BaseConverter):
    """A simple color class with a custom converter."""

    title: str = "Color"
    short_help: str = "A color name or hex code"
    help: str = "Available colors: red, green, blue, yellow."

    COLORS: ClassVar[dict[str, str]] = {
        "red": "#FF0000",
        "green": "#00FF00",
        "blue": "#0000FF",
        "yellow": "#FFFF00",
    }

    def __init__(self, name: str, hex_code: str) -> None:
        self.name: str = name
        self.hex_code: str = hex_code

    @classmethod
    @override
    async def cls_convert(
        cls,
        g_ctx: GlobalContext,
        event: CommandEvent,
        arg: str | object,
    ) -> Any:
        """Convert a color name to a Color object."""
        if not isinstance(arg, str):
            raise TypeError("Invalid type.")
        name_lower = arg.lower()
        if name_lower not in cls.COLORS:
            msg = f"Unknown color: {arg}. Valid colors: {', '.join(cls.COLORS.keys())}"
            raise ArgumentConversionError(msg)
        return Color(name_lower, cls.COLORS[name_lower])


async def transfer_verify(
    g_ctx: GlobalContext,
    ctx: CommandEvent,
    amount: int,
    user: str,
) -> str | bool:
    # aga = 10 / 0 # This line was problematic and removed
    if amount == 0:
        return "Amount must be greater than 0."
    if amount < 0:
        return "Amount must be positive."
    if user in [ctx.message.author_login, ctx.message.author_name]:
        return "You cannot transfer coins to yourself."
    return True


# Example data structure for remote callable methods
class ExampleData(Struct):
    """Example data structure for remote calls.

    Attributes:
        message: A message string
        timestamp: Unix timestamp
        value: A numeric value

    """

    message: str
    timestamp: int
    value: int


class ExampleCog(Cog, RemoteCallableMixin, FastAPIRoutesMixin):
    """Example cog showcasing new command features."""

    async def setup(self) -> None:
        """Set up the cog and register FastAPI routes."""
        await self.register_fastapi_routes()

    @api_route.get(APIServer.PUBLIC, "/health")
    async def health_check(self):
        """Health check endpoint for public server."""
        return {"status": "ok", "service": "ravenfall-channelbot"}

    @api_route.get(APIServer.PRIVATE, "/admin/status")
    async def admin_status(self):
        """Admin status endpoint for private server."""
        return {"status": "running", "admin": True}

    @command(name="echo", aliases=["echo1", "agecko"])
    async def echo(self, ctx: CommandEvent, message: str) -> None:
        """Echo a message back to the user.

        Args:
            message: The message to echo.

        Examples:
            !echo Hello World

        """
        await ctx.message.reply(f"You said: {message}")

    @command(name="multiply")
    async def multiply(self, ctx: CommandEvent, a: int, b: int) -> None:
        """Multiply two numbers.

        Args:
            a: First number.
            b: Second number.

        Examples:
            !multiply 5 7

        """
        result = a * b
        await ctx.message.reply(f"{a} × {b} = {result}")

    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    @command(name="divide")
    async def divide(
        self, ctx: CommandEvent, numerator: float, denominator: float
    ) -> None:
        """Divide two numbers.

        Args:
            numerator: The number to divide.
            denominator: The number to divide by.

        Examples:
            !divide 10 2
            !divide 7.5 2.5

        """
        if denominator == 0:
            await ctx.message.reply("Cannot divide by zero!")
            return
        result = numerator / denominator
        await ctx.message.reply(f"{numerator} ÷ {denominator} = {result:.2f}")

    @command(name="greet")
    async def greet(self, ctx: CommandEvent, username: str | None = None) -> None:
        """Greet a user.

        Args:
            username: The user to greet (defaults to command author).

        Examples:
            !greet
            !greet @borkedcube

        """
        target = username or ctx.message.author_name
        await ctx.message.reply(f"Hello, {target}! 👋")

    @command(name="setcolor")
    async def setcolor(self, ctx: CommandEvent, color: Color) -> None:
        """Set your favorite color.

        Examples:
            !setcolor red
            !setcolor blue

        """
        await ctx.message.reply(f"Set your color to {color.name} ({color.hex_code})")

    @verification(transfer_verify)
    @parameter("amount", description="Amount to transfer")
    @parameter("user", description="User to transfer to")
    @command(name="transfer", help_text="Transfer currency to another user")
    async def transfer(self, ctx: CommandEvent, amount: int, user: str) -> None:
        """Transfer coins to another user.

        Args:
            amount: The amount of coins to transfer.
            user: The user to transfer coins to.

        """
        await ctx.message.reply(f"Transferred {amount} coins to {user}.")

    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command(name="modcommand")
    async def modcommand(self, ctx: CommandEvent) -> None:
        """Only moderators and the bot owner can use this command.

        Examples:
            !modcommand

        """
        await ctx.message.reply("✅ You have moderator privileges!")

    @command(name="greetuser")
    async def greetuser(
        self,
        ctx: CommandEvent,
        username: str,
        *,
        greeting: str | None = "Hello",
    ) -> None:
        """Greet a user with a custom greeting.

        Args:
            username: The user to greet.
            greeting: Custom greeting message.

        Examples:
            !greetuser borkedcube
            !greetuser borkedcube --greeting="Welcome"
            !greetuser borkedcube greeting=Hi

        """
        await ctx.message.reply(f"{greeting}, {username}! 👋")

    @command(name="calculate")
    async def calculate(
        self, ctx: CommandEvent, operation: str, a: float, b: float
    ) -> None:
        """Perform a calculation.

        Args:
            operation: The operation (add, subtract, multiply, divide).
            a: First number.
            b: Second number.

        Examples:
            !calculate add 5 3
            !calculate multiply a=10 b=5
            !calculate divide --a 20 --b 4

        """
        op = operation.lower()
        if op == "add":
            result = a + b
            symbol = "+"
        elif op == "subtract":
            result = a - b
            symbol = "-"
        elif op == "multiply":
            result = a * b
            symbol = "×"
        elif op == "divide":
            if b == 0:
                await ctx.message.reply("Cannot divide by zero!")
                return
            result = a / b
            symbol = "÷"
        else:
            await ctx.message.reply(f"Unknown operation: {operation}")
            return
        await ctx.message.reply(f"{a} {symbol} {b} = {result}")

    @parameter("verbose", display_name="verb", aliases=["v", "debug"])
    @command(
        name="verbose_test",
        aliases=["verbose", "verb", "db"],
    )
    async def verbose_test(self, ctx: CommandEvent, verbose: bool = False) -> None:
        """Test boolean flags and argument aliases.

        Args:
            verbose (bool):
                Enable verbose mode (default: False).

        Examples:
            !verbose_test
            !verbose_test --verbose
            !verbose_test -v
            !verbose_test --debug

        """
        if verbose:
            await ctx.message.reply("Verbose mode enabled! 📝")
        else:
            await ctx.message.reply("Verbose mode disabled.")

    @parameter("rest", greedy=True, display_name="reast")
    @command(name="greedy_test")
    async def greedy_test(self, ctx: CommandEvent, first: str, rest: str) -> None:
        """Test greedy argument parsing.

        Args:
            first: The first word.
            rest: The rest of the message (greedy).

        Examples:
            !greedy_test Hello world this is a test

        """
        await ctx.message.reply(f"First: '{first}', Rest: '{rest}'")

    @checks(HasRole(UserRole.BOT_ADMINISTRATOR))
    @command(name="owner_only", aliases=["owner only"])
    async def owner_only_command(self, ctx: CommandEvent) -> None:
        """Only the bot owner can use this command.

        This demonstrates using a single check in the @checks decorator.

        Examples:
            !owner_only

        """
        await ctx.message.reply("✅ You are the bot owner!")

    @checks(
        HasRole(UserRole.BOT_ADMINISTRATOR, UserRole.MODERATOR, UserRole.ADMINISTRATOR),
    )
    @command(name="multi_check")
    async def multi_check_command(self, ctx: CommandEvent) -> None:
        """Demonstrates multiple checks.

        This command requires BOTH moderator AND bot owner privileges.
        Note: In practice, you'd usually want OR logic, but this shows
        how to pass multiple check functions to @checks().

        Examples:
            !multi_check

        """
        await ctx.message.reply("✅ You passed all checks!")

    @parameter("item_name", description="The name of the item", regex=r"^[a-zA-Z ]+$")
    @parameter("amount", description="The amount of the item")
    @command()
    async def item_amount(self, ctx: CommandEvent, item_name: str, amount: int) -> None:
        """Display the amount of an item.

        Args:
            ctx: The command event context
            item_name: The name of the item
            amount: The amount of the item

        """
        await ctx.message.reply(f"You have {amount} of {item_name}.")

    @cooldown(1, 10, [BucketType.USER, BucketType.CHANNEL])
    @command()
    async def cooldown_test(self, ctx: CommandEvent) -> None:
        await ctx.message.reply("buh")

    @cooldown(1, 90, [BucketType.USER, BucketType.CHANNEL])
    @command()
    async def cooldown_test_long(self, ctx: CommandEvent) -> None:
        await ctx.message.reply("buh (long cooldown)")

    # Remote callable demonstration
    @remote_callable()
    def get_example_data(self, value: int = 42) -> ExampleData:
        """Get example data that can be called locally or remotely.

        This method demonstrates the remote_callable decorator. It can be
        called directly (local) or via .call_remote() to fetch data from
        another bot instance.

        Args:
            value: A numeric value to include in the response

        Returns:
            ExampleData: Struct containing message, timestamp, and value

        """
        return ExampleData(
            message="Hello from remote bot!",
            timestamp=int(time.time()),
            value=value,
        )

    @command()
    async def remote_test(self, ctx: CommandEvent) -> None:
        """Test remote callable functionality.

        Demonstrates local vs remote calls to remote_callable methods.

        Examples:
            !remote_test

        """
        # Local call - no HTTP, no conversion
        local_data = self.get_example_data(100)
        await ctx.message.reply(
            f"Local call: {local_data.message}, value={local_data.value}"
        )

        # Remote call example (commented out since no remote bot is configured)
        # remote_service = self.global_context.get_service(RemoteBotService)
        # remote_bot = remote_service.get_remote_bot("bot2")
        # remote_data = await self.get_example_data.call_remote(remote_bot, 200)
        # await ctx.message.reply(
        #     f"Remote call: {remote_data.message}, value={remote_data.value}"
        # )

    async def long_cooldown_test(self, ctx: CommandEvent) -> None:
        await ctx.message.reply("buh")

    @command(aliases=["args test"])
    async def args_test(self, ctx: CommandEvent, *args: str) -> None:
        await ctx.message.reply(f"Args: {args}")
