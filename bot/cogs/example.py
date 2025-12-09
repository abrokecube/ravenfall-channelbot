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
from ..cog import Cog
from ..commands import (
    Context, 
    Commands, 
    UserRole, 
    checks, 
    parameter, 
    cooldown,
    Converter, 
    Check, 
    verification, 
    CommandError
)
from ..command_exceptions import ArgumentConversionError, CheckFailure
from ..command_contexts import TwitchRedeemContext
from ..command_utils import HasRole
from ..command_enums import BucketType

# Example custom type with converter
class Color(Converter):
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
    async def convert(cls, ctx: Context, arg: str) -> 'Color':
        """Convert a color name to a Color object."""
        name_lower = arg.lower()
        if name_lower not in cls.COLORS:
            raise ArgumentConversionError(f"Unknown color: {arg}. Valid colors: {', '.join(cls.COLORS.keys())}")
        return Color(name_lower, cls.COLORS[name_lower])

class ExampleCog(Cog):
    """Example cog showcasing new command features."""
    
    @Cog.listener("custom_event")
    async def on_custom_event(self, ctx: Context, data: str):
        """Example of a generic event listener."""
        print(f"Custom event received: {data}")
        await ctx.send(f"Custom event received: {data}")

    @Cog.redeem(name="test redeem")
    async def hydrate_redeem(self, ctx: TwitchRedeemContext, *args):
        """Example of a simple redeem without parameters."""
        await ctx.send(f"{ctx.author} says: Stay hydrated! (args: {args})")

    # @Cog.redeem(name="Highlight My Message")
    # async def highlight_redeem(self, ctx: TwitchRedeemContext, message: str):
    #     """Example of a redeem with a parameter (user input)."""
    #     await ctx.send(f"Highlighting: {message}")
    #     # In a real scenario, you might do something with the message
        
    # @Cog.redeem(name="Gamble")
    # async def gamble_redeem(self, ctx: TwitchRedeemContext, amount: int):
    #     """Example of a redeem with a typed parameter."""
    #     await ctx.send(f"{ctx.author} gambled {amount} points!")
    
    @Cog.command(name="echo")
    async def echo(self, ctx: Context, message: str):
        """Echo a message back to the user.
        
        Args:
            message: The message to echo.
            
        Examples:
            !echo Hello World
        """
        await ctx.reply(f"You said: {message}")
    
    @Cog.command(name="multiply")
    async def multiply(self, ctx: Context, a: int, b: int):
        """Multiply two numbers.
        
        Args:
            a: First number.
            b: Second number.
            
        Examples:
            !multiply 5 7
        """
        result = a * b
        await ctx.reply(f"{a} × {b} = {result}")
    
    @Cog.command(name="divide")
    @checks(HasRole(UserRole.BOT_OWNER))
    async def divide(self, ctx: Context, numerator: float, denominator: float):
        """Divide two numbers.
        
        Args:
            numerator: The number to divide.
            denominator: The number to divide by.
            
        Examples:
            !divide 10 2
            !divide 7.5 2.5
        """
        if denominator == 0:
            await ctx.reply("Cannot divide by zero!")
            return
        result = numerator / denominator
        await ctx.reply(f"{numerator} ÷ {denominator} = {result:.2f}")
    
    @Cog.command(name="greet")
    async def greet(self, ctx: Context, username: Optional[str] = None):
        """Greet a user.
        
        Args:
            username: The user to greet (defaults to command author).
            
        Examples:
            !greet
            !greet @borkedcube
        """
        target = username or ctx.author
        await ctx.reply(f"Hello, {target}! 👋")
    
    @Cog.command(name="setcolor")
    @parameter('color', converter=Color)
    async def setcolor(self, ctx: Context, color: str):
        """Set your favorite color.
                    
        Examples:
            !setcolor red
            !setcolor blue
        """
        await ctx.reply(f"Set your color to {color.name} ({color.hex_code})")
    
    async def transfer_verify(ctx: Context, amount: int, user: str):
        # aga = 10 / 0 # This line was problematic and removed
        if amount == 0:
            return "Amount must be greater than 0."
        if amount < 0:
            return "Amount must be positive."
        if user == ctx.author:
            return "You cannot transfer coins to yourself."
        return True

    @Cog.command(name="transfer", help="Transfer currency to another user")
    @verification(transfer_verify)
    @parameter("amount", help="Amount to transfer")
    @parameter("user", help="User to transfer to")
    async def transfer(self, ctx: Context, amount: int, user: str):
        """Transfer coins to another user.
        
        Args:
            amount: The amount of coins to transfer.
            user: The user to transfer coins to.
        """
        await ctx.reply(f"Transferred {amount} coins to {user}.")


    @Cog.command(name="modcommand")
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR))
    async def modcommand(self, ctx: Context):
        """A command only moderators and the bot owner can use.
        
        Examples:
            !modcommand
        """
        await ctx.reply("✅ You have moderator privileges!")
        
    @Cog.command(name="greetuser")
    async def greetuser(self, ctx: Context, username: str, *, greeting: Optional[str] = "Hello"):
        """Greet a user with a custom greeting.
        
        Args:
            username: The user to greet.
            greeting: Custom greeting message.
            
        Examples:
            !greetuser borkedcube
            !greetuser borkedcube --greeting="Welcome"
            !greetuser borkedcube greeting=Hi
        """
        await ctx.reply(f"{greeting}, {username}! 👋")
    
    @Cog.command(name="calculate")
    async def calculate(self, ctx: Context, operation: str, a: float, b: float):
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
                await ctx.reply("Cannot divide by zero!")
                return
            result = a / b
            symbol = "÷"
        else:
            await ctx.reply(f"Unknown operation: {operation}")
            return
        await ctx.reply(f"{a} {symbol} {b} = {result}")

    @Cog.command(
        name="verbose_test", 
        aliases=["verbose", "verb", "db"],
    )
    @parameter("verbose", display_name="verb", aliases=['v', 'debug'])
    async def verbose_test(self, ctx: Context, verbose: bool = False):
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
            await ctx.reply("Verbose mode enabled! 📝")
        else:
            await ctx.reply("Verbose mode disabled.")

    @Cog.command(name="greedy_test")
    @parameter("rest", greedy=True, display_name="reast")
    async def greedy_test(self, ctx: Context, first: str, rest: str):
        """Test greedy argument parsing.
        
        Args:
            first: The first word.
            rest: The rest of the message (greedy).
            
        Examples:
            !greedy_test Hello world this is a test
        """
        await ctx.reply(f"First: '{first}', Rest: '{rest}'")

    @Cog.command(name="owner_only", aliases=["owner only"])
    @checks(HasRole(UserRole.BOT_OWNER))
    async def owner_only_command(self, ctx: Context):
        """A command only the bot owner can use.
        
        This demonstrates using a single check in the @checks decorator.
        
        Examples:
            !owner_only
        """
        await ctx.reply("✅ You are the bot owner!")

    @Cog.command(name="multi_check")
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.MODERATOR, UserRole.ADMIN))
    async def multi_check_command(self, ctx: Context):
        """A command that demonstrates multiple checks.
        
        This command requires BOTH moderator AND bot owner privileges.
        Note: In practice, you'd usually want OR logic, but this shows
        how to pass multiple check functions to @checks().
        
        Examples:
            !multi_check
        """
        await ctx.reply("✅ You passed all checks!")

    @Cog.command()
    @parameter("item_name", help="The name of the item", regex=r'^[a-zA-Z ]+$')
    @parameter("amount", help="The amount of the item")
    async def item_amount(self, ctx: Context, item_name: str, amount: int):
        await ctx.reply(f"You have {amount} of {item_name}.")

    @Cog.command()
    @cooldown(1, 10, [BucketType.USER, BucketType.CHANNEL])
    async def cooldown_test(self, ctx: Context):
        await ctx.reply("buh")

    @Cog.command()
    @cooldown(1, 90, [BucketType.USER, BucketType.CHANNEL])
    async def long_cooldown_test(self, ctx: Context):
        await ctx.reply("buh")

    @Cog.command(aliases=["args test"])
    async def args_test(self, ctx: Context, *args: str):
        await ctx.reply(f"Args: {args}")

def setup(commands: Commands, **kwargs) -> None:
    """Load the example cog.
    
    Args:
        commands: The Commands instance to register commands with.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(ExampleCog, **kwargs)
