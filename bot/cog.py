from typing import Dict, Type, Optional, TypeVar, Any, Callable, Awaitable, Union, List, TYPE_CHECKING
from functools import wraps

# Import the necessary types from commands.py
from .commands import Command, CommandFunc, Commands, TwitchRedeem, TwitchRedeemFunc, EventListener

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
        self.commands: Dict[str, Command] = {}
        self.redeems: Dict[str, TwitchRedeem] = {}
        self.listeners: Dict[str, Dict[str, EventListener]] = {}
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
    def _create_listener_decorator(cls, event_type: str, name: Optional[str] = None, **kwargs) -> Callable[[Callable], Callable]:
        """Helper to create a listener decorator."""
        def decorator(func: Callable) -> Callable:
            listener_name = name or func.__name__.lower()
            
            # Store the listener information in the function itself
            if not hasattr(func, '_cog_listener_info'):
                func._cog_listener_info = []
            
            # Store the listener name and kwargs with the function
            func._cog_listener_info.append((event_type, listener_name, kwargs))
            
            return func
        return decorator

    @classmethod
    def command(cls, name: Optional[str] = None, **kwargs) -> Callable[[Callable], Callable]:
        """Decorator to register a command in the cog."""
        return cls._create_listener_decorator("command", name, **kwargs)

    @classmethod
    def redeem(cls, name: Optional[str] = None, **kwargs) -> Callable[[Callable], Callable]:
        """Decorator to register a channel point redemption in the cog."""
        return cls._create_listener_decorator("twitch_redeem", name, **kwargs)
    
    @classmethod
    def listener(cls, event_type: str, name: Optional[str] = None, **kwargs) -> Callable[[Callable], Callable]:
        """Decorator to register a generic event listener in the cog."""
        return cls._create_listener_decorator(event_type, name, **kwargs)
    
    @classmethod
    def create_instance(cls: Type[TCog], bot: Commands, **kwargs) -> TCog:
        """Create an instance of the cog and register its commands.
        
        Args:
            **kwargs: Additional arguments to pass to the cog's __init__
            
        Returns:
            An instance of the cog
        """
        instance = cls(**kwargs)
        instance_commands = {}
        instance_redeems = {}
        instance_listeners = {}
        
        # Find all methods that have listener info
        for attr_name in dir(instance):
            attr = getattr(instance, attr_name)
            if hasattr(attr, '_cog_listener_info'):
                for event_type, listener_name, listener_kwargs in attr._cog_listener_info:
                    # Create a bound method for the listener
                    bound_method = attr.__get__(instance, cls)
                    
                    # Create the appropriate object based on event_type
                    listener = None
                    if event_type == "command":
                        listener = Command(
                            name=listener_name,
                            func=bound_method,
                            bot=bot,
                            cog=instance,
                            **listener_kwargs
                        )
                        instance_commands[listener_name] = listener
                    elif event_type == "twitch_redeem":
                        listener = TwitchRedeem(
                            name=listener_name,
                            func=bound_method,
                            bot=bot,
                            **listener_kwargs
                        )
                        instance_redeems[listener_name] = listener
                    else:
                        listener = EventListener(
                            name=listener_name,
                            func=bound_method,
                            event_type=event_type,
                            **listener_kwargs
                        )
                    
                    if event_type not in instance_listeners:
                        instance_listeners[event_type] = {}
                    instance_listeners[event_type][listener_name] = listener

        # Set the instance's commands, redeems, and listeners
        instance.commands = instance_commands
        instance.redeems = instance_redeems
        instance.listeners = instance_listeners
        
        return instance

class CogManager:
    """Manages loading and unloading of cogs."""
    
    def __init__(self, bot: Commands):
        self.bot = bot
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
        cog = cog_cls.create_instance(self.bot, **kwargs)
        
        # Register all commands from the cog
        try:
            # for name, command in cog.commands.items():
            #     self.bot.add_command_object(name, command)
                    
            # for name, redeem in cog.redeems.items():
            #     self.bot.add_redeem_object(name, redeem)
                
            for event_type, listeners in cog.listeners.items():
                for name, listener in listeners.items():
                    self.bot.add_listener(listener)
        except ValueError as e:
            # Add cog context to the error message
            raise ValueError(f"{e} (in cog '{cog.name}')")
        
        # Store the cog and run setup
        self.loaded_cogs[cog.name] = cog
        self.bot.loop.create_task(cog.setup(self.bot))
    
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
        for cmd_name, cmd in list(self.bot.commands.items()):
            if cmd.func.__self__ == cog:  # type: ignore
                self.bot.commands.pop(cmd_name)
    
    def reload_cog(self, cog_cls: Type[Cog], **kwargs) -> None:
        """Reload a cog by unloading and then loading it again.
        
        Args:
            cog_cls: The cog class to reload.
            **kwargs: Additional arguments to pass to the cog's __init__
        """
        if cog_cls.name in self.loaded_cogs:
            self.unload_cog(cog_cls.name)
        self.load_cog(cog_cls, **kwargs)
