from typing import Dict, Type, Optional, TypeVar, Any, Callable, Awaitable, Union, List, TYPE_CHECKING
from functools import wraps

# Import the necessary types from commands.py
from .commands import Command, CommandFunc, Commands, Redeem, RedeemFunc

# Type variable for the cog class
TCog = TypeVar('TCog', bound='Cog')

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
    def __init_subclass__(cls, **kwargs):
        """Automatically set the name of the cog to the class name."""
        super().__init_subclass__(**kwargs)
        cls.name = cls.__name__.lower()
        
    def __init__(self, description: str = "", config: Optional[Dict[str, Any]] = None, **kwargs):
        """Initialize the cog with optional bot and config references.
        
        Args:
            description: Optional description of the cog
            config: Optional configuration dictionary
            **kwargs: Additional keyword arguments to store as attributes
        """
        self.name = self.__class__.__name__.lower()
        self.description = description
        self.commands = {}
        self.bot = None
        self.config = config or {}
        
        # Store any additional attributes
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
            
            # Store the command information in the function itself
            if not hasattr(func, '_cog_command_info'):
                func._cog_command_info = []
            
            # Store the command name and kwargs with the function
            func._cog_command_info.append((cmd_name, kwargs))
            
            # Return the original function so it can still be accessed normally
            return func
            
        return decorator

    @classmethod
    def redeem(cls, name: Optional[str] = None, **kwargs) -> Callable[[RedeemFunc], RedeemFunc]:
        """Decorator to register a redeem in the cog.
        
        Args:
            name: Optional redeem name. If not provided, uses the function name.
            **kwargs: Additional keyword arguments to pass to the Redeem constructor.
            
        Returns:
            A decorator that registers the redeem.
        """
        def decorator(func: RedeemFunc) -> RedeemFunc:
            redeem_name = name or func.__name__.lower()
            
            # Store the redeem information in the function itself
            if not hasattr(func, '_cog_redeem_info'):
                func._cog_redeem_info = []
            
            # Store the redeem name and kwargs with the function
            func._cog_redeem_info.append((redeem_name, kwargs))
            
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
        instance_commands = {}
        
        # Find all methods that have command info
        for attr_name in dir(instance):
            attr = getattr(instance, attr_name)
            if hasattr(attr, '_cog_command_info'):
                for cmd_name, cmd_kwargs in attr._cog_command_info:
                    # Create a bound method for the command
                    bound_method = attr.__get__(instance, cls)
                    # Create a Command object
                    command = Command(
                        name=cmd_name,
                        func=bound_method,
                        **cmd_kwargs
                    )
                    instance_commands[cmd_name] = command
            
        # Find all methods that have redeem info
        for attr_name in dir(instance):
            attr = getattr(instance, attr_name)
            if hasattr(attr, '_cog_redeem_info'):
                for redeem_name, redeem_kwargs in attr._cog_redeem_info:
                    # Create a bound method for the redeem
                    bound_method = attr.__get__(instance, cls)
                    # Create a Redeem object
                    redeem = Redeem(
                        name=redeem_name,
                        func=bound_method,
                        **redeem_kwargs
                    )
                    instance_commands[redeem_name] = redeem
        
        # Set the instance's commands
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
        try:
            for name, command in cog.commands.items():
                self.commands.add_command(name, command.func)
                for alias in command.aliases:
                    self.commands.add_command(alias, command.func)
        except ValueError as e:
            # Add cog context to the error message
            raise ValueError(f"{e} (in cog '{cog.name}')")
        
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
