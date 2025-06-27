from typing import Dict, Type, Optional, TypeVar, Any, Callable, Awaitable, Union, List
from dataclasses import dataclass, field
from functools import wraps

# Import the necessary types from commands.py
from .commands import Command, CommandFunc, Context, Commands

# Type variable for the cog class
TCog = TypeVar('TCog', bound='Cog')

@dataclass
class Cog:
    """Base class for command cogs.
    
    Cogs are used to group related commands and their associated state.
    
    Attributes:
        name: The name of the cog (auto-generated from class name)
        description: Optional description of the cog
        commands: Dictionary of registered commands
        bot: Reference to the bot instance
        config: Reference to the bot's configuration
    """
    name: str = field(init=False)
    description: str = field(default="")
    commands: Dict[str, Command] = field(default_factory=dict, init=False)
    bot: Any = field(default=None)
    config: Dict[str, Any] = field(default_factory=dict)
    
    def __init_subclass__(cls, **kwargs):
        """Automatically set the name of the cog to the class name."""
        super().__init_subclass__(**kwargs)
        cls.name = cls.__name__.lower()
        
    def __init__(self, **kwargs):
        """Initialize the cog with optional bot and config references.
        
        Args:
            **kwargs: Additional keyword arguments to store as attributes
        """
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    async def setup(self, commands: Commands) -> None:
        """Called when the cog is loaded.
        
        Args:
            commands: The Commands instance to register commands with.
        """
        # The commands are already registered through the Commands.add_command calls
        pass
    
    @classmethod
    def command(cls, name: Optional[str] = None, **kwargs) -> Callable[[CommandFunc], CommandFunc]:
        """Decorator to register a command in the cog.
        
        Args:
            name: Optional command name. If not provided, uses the function name.
            **kwargs: Additional keyword arguments to pass to the Command constructor.
            
        Returns:
            A decorator that registers the command.
        """
        def decorator(func: CommandFunc) -> CommandFunc:
            cmd_name = name or func.__name__.lower()
            
            # Store the command in the class's commands dictionary
            if not hasattr(cls, '_commands'):
                cls._commands = {}
            
            # Store the command with a wrapper that will be bound to the instance
            cls._commands[cmd_name] = {
                'func': func,
                'kwargs': kwargs
            }
            
            # Return the original function so it can still be accessed normally
            return func
            
        return decorator
    
    @classmethod
    def create_instance(cls: Type[TCog], **kwargs) -> TCog:
        """Create an instance of the cog and register its commands.
        
        Args:
            **kwargs: Additional arguments to pass to the cog's __init__
            
        Returns:
            An instance of the cog
        """
        instance = cls(**kwargs)
        
        # Get the class commands
        class_commands = getattr(cls, '_commands', {})
        instance_commands = {}
        
        # Create bound methods for each command
        for cmd_name, cmd_data in class_commands.items():
            # Create a bound method for the command
            func = cmd_data['func'].__get__(instance, cls)
            # Create the command with the bound method
            instance_commands[cmd_name] = Command(
                name=cmd_name,
                func=func,
                **cmd_data['kwargs']
            )
            
        instance.commands = instance_commands
        return instance

class CogManager:
    """Manages loading and unloading of cogs."""
    
    def __init__(self, commands: Commands):
        self.commands = commands
        self.loaded_cogs: Dict[str, Cog] = {}
    
    def load_cog(self, cog_cls: Type[Cog], **kwargs) -> None:
        """Load a cog and register its commands.
        
        Args:
            cog_cls: The cog class to load.
            **kwargs: Additional arguments to pass to the cog's __init__
            
        Raises:
            RuntimeError: If the cog is already loaded.
        """
        if cog_cls.name in self.loaded_cogs:
            raise RuntimeError(f"Cog '{cog_cls.name}' is already loaded.")
            
        # Create the cog instance
        cog = cog_cls.create_instance(**kwargs)
        
        # Register all commands from the cog
        for name, command in cog.commands.items():
            self.commands.add_command(name, command.func)
        
        # Store the cog and run setup
        self.loaded_cogs[cog.name] = cog
        self.commands.loop.create_task(cog.setup(self.commands))
    
    def unload_cog(self, cog_name: str) -> None:
        """Unload a cog and remove its commands.
        
        Args:
            cog_name: The name of the cog to unload.
            
        Raises:
            RuntimeError: If the cog is not found.
        """
        if cog_name not in self.loaded_cogs:
            raise RuntimeError(f"Cog '{cog_name}' is not loaded.")
            
        cog = self.loaded_cogs.pop(cog_name)
        # Remove all commands registered by this cog
        for cmd_name, cmd in list(self.commands.commands.items()):
            if cmd.func.__self__ == cog:  # type: ignore
                self.commands.commands.pop(cmd_name)
    
    def reload_cog(self, cog_cls: Type[Cog], **kwargs) -> None:
        """Reload a cog by unloading and then loading it again.
        
        Args:
            cog_cls: The cog class to reload.
            **kwargs: Additional arguments to pass to the cog's __init__
        """
        if cog_cls.name in self.loaded_cogs:
            self.unload_cog(cog_cls.name)
        self.load_cog(cog_cls, **kwargs)
