from typing import Optional, Dict, Any
from ..commands import Context, Commands
from ..cog import Cog

class ExampleCog(Cog):
    """An example cog with some basic commands.
    
    This cog demonstrates how to use global objects and configuration.
    """
    
    def __init__(self, **kwargs):
        """Initialize the cog with optional configuration.
        
        Args:
            **kwargs: Additional arguments passed from load_cog().
                     Common ones include 'bot', 'config', etc.
        """
        super().__init__(**kwargs)
        self.counter = 0
        self.config: Dict[str, Any] = getattr(self, 'config', {})
        self.bot: Optional[Commands] = getattr(self, 'bot', None)
    
    @Cog.command(name="hello", help="Says hello to the user")
    async def hello_command(self, ctx: Context):
        """A simple hello command."""
        await ctx.reply(f"Hello, {ctx.msg.user.name}!")
    
    @Cog.command(aliases=["count"], help="Shows the current counter value")
    async def counter(self, ctx: Context):
        """Show the current counter value."""
        await ctx.reply(f"Counter: {self.counter}")
    
    @Cog.command(name="add", help="Add a number to the counter")
    async def add_to_counter(self, ctx: Context):
        """Add a number to the counter."""
        try:
            amount = int(ctx.args.get(0, "1"))
            self.counter += amount
            await ctx.reply(f"Added {amount}. New counter: {self.counter}")
        except ValueError:
            await ctx.reply("Please provide a valid number to add.")
    
    @Cog.command(name="echo", help="Echoes the provided text")
    async def echo(self, ctx: Context):
        """Echo the remaining text."""
        text = ctx.parameter
        if not text:
            await ctx.reply("Please provide some text to echo!")
            return
            
        await ctx.send(f"Echo: {text}")

def setup(commands: Commands, **kwargs) -> None:
    """Load the example cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        **kwargs: Additional arguments to pass to the cog.
                Common ones include:
                - rf: The Ravenfall API instance
                - config: Configuration dictionary
    """
    # Pass any additional arguments to the cog
    commands.load_cog(ExampleCog, **kwargs)
