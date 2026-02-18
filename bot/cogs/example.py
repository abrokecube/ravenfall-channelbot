"""
Example Cog

This cog demonstrates the new command system features:
- Typed arguments with automatic parsing
- Custom type converters
- Custom checks using @checks(check1, check2, ...)
- Google-style docstrings for documentation
"""

from __future__ import annotations
from typing import Optional

from ..commands.cog import Cog
from ..commands.events import CommandEvent
from ..commands.converters import BaseConverter
from ..commands.exceptions import (
    ArgumentConversionError
)
from ..commands.decorators import (
    command,
    checks,
    parameter,
    verification,
    cooldown
)
from ..commands.checks import (
    HasRole,
    MinPermissionLevel
)
from ..commands.enums import UserRole, BucketType


# Example custom type with converter
class Color(BaseConverter):
    """A simple color class with a custom converter."""
    
    title = "Color"
    short_help = "A color name or hex code"
    help = "Available colors: red, green, blue, yellow."

    COLORS = {
        'red': '#FF0000',
        'green': '#00FF00',
        'blue': '#0000FF',
        'yellow': '#FFFF00',
    }
    
    def __init__(self, name: str, hex_code: str):
        self.name = name
        self.hex_code = hex_code
    
    @classmethod
    async def convert(cls, ctx: CommandEvent, arg: str) -> 'Color':
        """Convert a color name to a Color object."""
        name_lower = arg.lower()
        if name_lower not in cls.COLORS:
            raise ArgumentConversionError(f"Unknown color: {arg}. Valid colors: {', '.join(cls.COLORS.keys())}")
        return Color(name_lower, cls.COLORS[name_lower])

class ExampleCog(Cog):
    """Example cog showcasing new command features."""
    
    # @Cog.redeem(name="test redeem")
    # async def hydrate_redeem(self, ctx: TwitchRedeemCommandEvent, *args):
    #     """Example of a simple redeem without parameters."""
    #     await ctx.message.send(f"{ctx.author} says: Stay hydrated! (args: {args})")
    
    @command(name="echo", aliases=['echo1', 'agecko'])
    async def echo(self, ctx: CommandEvent, message: str):
        """Echo a message back to the user.
        
        Args:
            message: The message to echo.
            
        Examples:
            !echo Hello World
        """
        await ctx.message.reply(f"You said: {message}")
    
    @command(name="multiply")
    async def multiply(self, ctx: CommandEvent, a: int, b: int):
        """Multiply two numbers.
        
        Args:
            a: First number.
            b: Second number.
            
        Examples:
            !multiply 5 7
        """
        result = a * b
        await ctx.message.reply(f"{a} × {b} = {result}")
    
    @command(name="divide")
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def divide(self, ctx: CommandEvent, numerator: float, denominator: float):
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
    async def greet(self, ctx: CommandEvent, username: Optional[str] = None):
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
    async def setcolor(self, ctx: CommandEvent, color: Color):
        """Set your favorite color.
                    
        Examples:
            !setcolor red
            !setcolor blue
        """
        await ctx.message.reply(f"Set your color to {color.name} ({color.hex_code})")
    
    async def transfer_verify(ctx: CommandEvent, amount: int, user: str):
        # aga = 10 / 0 # This line was problematic and removed
        if amount == 0:
            return "Amount must be greater than 0."
        if amount < 0:
            return "Amount must be positive."
        if user in [ctx.message.author_login, ctx.message.author_name]:
            return "You cannot transfer coins to yourself."
        return True

    @command(name="transfer", help="Transfer currency to another user")
    @verification(transfer_verify)
    @parameter("amount", help="Amount to transfer")
    @parameter("user", help="User to transfer to")
    async def transfer(self, ctx: CommandEvent, amount: int, user: str):
        """Transfer coins to another user.
        
        Args:
            amount: The amount of coins to transfer.
            user: The user to transfer coins to.
        """
        await ctx.message.reply(f"Transferred {amount} coins to {user}.")


    @command(name="modcommand")
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def modcommand(self, ctx: CommandEvent):
        """A command only moderators and the bot owner can use.
        
        Examples:
            !modcommand
        """
        await ctx.message.reply("✅ You have moderator privileges!")
        
    @command(name="greetuser")
    async def greetuser(self, ctx: CommandEvent, username: str, *, greeting: Optional[str] = "Hello"):
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
    async def calculate(self, ctx: CommandEvent, operation: str, a: float, b: float):
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

    @command(
        name="verbose_test", 
        aliases=["verbose", "verb", "db"],
    )
    @parameter("verbose", display_name="verb", aliases=['v', 'debug'])
    async def verbose_test(self, ctx: CommandEvent, verbose: bool = False):
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

    @command(name="greedy_test")
    @parameter("rest", greedy=True, display_name="reast")
    async def greedy_test(self, ctx: CommandEvent, first: str, rest: str):
        """Test greedy argument parsing.
        
        Args:
            first: The first word.
            rest: The rest of the message (greedy).
            
        Examples:
            !greedy_test Hello world this is a test
        """
        await ctx.message.reply(f"First: '{first}', Rest: '{rest}'")

    @command(name="owner_only", aliases=["owner only"])
    @checks(HasRole(UserRole.BOT_ADMINISTRATOR))
    async def owner_only_command(self, ctx: CommandEvent):
        """A command only the bot owner can use.
        
        This demonstrates using a single check in the @checks decorator.
        
        Examples:
            !owner_only
        """
        await ctx.message.reply("✅ You are the bot owner!")

    @command(name="multi_check")
    @checks(HasRole(UserRole.BOT_ADMINISTRATOR, UserRole.MODERATOR, UserRole.ADMINISTRATOR))
    async def multi_check_command(self, ctx: CommandEvent):
        """A command that demonstrates multiple checks.
        
        This command requires BOTH moderator AND bot owner privileges.
        Note: In practice, you'd usually want OR logic, but this shows
        how to pass multiple check functions to @checks().
        
        Examples:
            !multi_check
        """
        await ctx.message.reply("✅ You passed all checks!")

    @command()
    @parameter("item_name", help="The name of the item", regex=r'^[a-zA-Z ]+$')
    @parameter("amount", help="The amount of the item")
    async def item_amount(self, ctx: CommandEvent, item_name: str, amount: int):
        await ctx.message.reply(f"You have {amount} of {item_name}.")

    @command()
    @cooldown(1, 10, [BucketType.USER, BucketType.CHANNEL])
    async def cooldown_test(self, ctx: CommandEvent):
        await ctx.message.reply("buh")

    @command()
    @cooldown(1, 90, [BucketType.USER, BucketType.CHANNEL])
    async def long_cooldown_test(self, ctx: CommandEvent):
        await ctx.message.reply("buh")

    @command(aliases=["args test"])
    async def args_test(self, ctx: CommandEvent, *args: str):
        await ctx.message.reply(f"Args: {args}")
