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
from ..commands import Context, Commands, UserRole, checks, ArgumentParsingError, parameter

# Example custom type with converter
class Color:
    """A simple color class with a custom converter."""
    
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
            raise ArgumentParsingError(f"Unknown color: {arg}. Valid colors: {', '.join(cls.COLORS.keys())}")
        return Color(name_lower, cls.COLORS[name_lower])

# Example custom checks
def is_moderator(ctx: Context) -> bool:
    if UserRole.MODERATOR not in ctx.roles:
        return "❌ This command requires moderator privileges."
    return True

def is_bot_owner(ctx: Context) -> bool:
    if UserRole.BOT_OWNER not in ctx.roles:
        return "❌ This command requires bot owner privileges."
    return True

def is_moderator_or_owner(ctx: Context) -> bool:
    if not (UserRole.MODERATOR in ctx.roles or 
            UserRole.BOT_OWNER in ctx.roles):
        return "❌ This command requires moderator privileges."
    return True

class ExampleCog(Cog):
    """Example cog showcasing new command features."""
    
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
    async def setcolor(self, ctx: Context, color: Color):
        """Set your favorite color.
        
        Args:
            color: Your favorite color (red, green, blue, or yellow).
            
        Examples:
            !setcolor red
            !setcolor blue
        """
        await ctx.reply(f"Set your color to {color.name} ({color.hex_code})")
    
    @Cog.command(name="modcommand")
    @checks(is_moderator_or_owner)
    async def modcommand(self, ctx: Context):
        """A command only moderators and the bot owner can use.
        
        Examples:
            !modcommand
        """
        await ctx.reply("✅ You have moderator privileges!")
    
    @Cog.command(name="roles")
    async def roles(self, ctx: Context):
        """Show your current roles.
        
        Examples:
            !roles
        """
        role_names = [role.value for role in ctx.roles]
        await ctx.reply(f"Your roles: {', '.join(role_names)}")
    
    @Cog.command(name="greetuser")
    async def greetuser(self, ctx: Context, username: str, *, greeting: Optional[str] = "Hello"):
        """Greet a user with a custom greeting.
        
        Args:
            username: The user to greet.
            greeting: Custom greeting message (keyword-only, optional).
            
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
    @parameter("verbose", aliases=['v', 'debug'])
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
    @parameter("rest", greedy=True)
    async def greedy_test(self, ctx: Context, first: str, rest: str):
        """Test greedy argument parsing.
        
        Args:
            first: The first word.
            rest: The rest of the message (greedy).
            
        Examples:
            !greedy_test Hello world this is a test
        """
        await ctx.reply(f"First: '{first}', Rest: '{rest}'")

    @Cog.command(name="owner_only")
    @checks(is_bot_owner)
    async def owner_only_command(self, ctx: Context):
        """A command only the bot owner can use.
        
        This demonstrates using a single check in the @checks decorator.
        
        Examples:
            !owner_only
        """
        await ctx.reply("✅ You are the bot owner!")

    @Cog.command(name="multi_check")
    @checks(is_moderator, is_bot_owner)
    async def multi_check_command(self, ctx: Context):
        """A command that demonstrates multiple checks.
        
        This command requires BOTH moderator AND bot owner privileges.
        Note: In practice, you'd usually want OR logic, but this shows
        how to pass multiple check functions to @checks().
        
        Examples:
            !multi_check
        """
        await ctx.reply("✅ You passed all checks!")

def setup(commands: Commands, **kwargs) -> None:
    """Load the example cog.
    
    Args:
        commands: The Commands instance to register commands with.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(ExampleCog, **kwargs)
