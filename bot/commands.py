from __future__ import annotations

import asyncio
import logging
import inspect
import os
from typing import Callable, List, Dict, Awaitable, Union, Optional, TYPE_CHECKING, Protocol, runtime_checkable, Any, Type, cast, get_origin, get_args
from twitchAPI.twitch import Twitch
from twitchAPI.chat import ChatMessage, Chat
from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionData
from dataclasses import dataclass
import re
from enum import Enum

from .doc_parser import parse_google_docstring

# Configure logger for this module
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .cog import Cog, CogManager

class UserRole(Enum):
    BOT_OWNER = "bot_owner"
    ADMIN = "admin"
    MODERATOR = "moderator"
    SUBSCRIBER = "subscriber"
    USER = "user"

class OutputMessageType(Enum):
    SINGLE_LINE = "single_line"
    MULTI_LINE = "multiple_lines"

class CommandError(Exception):
    """Base exception for command-related errors."""
    def __init__(self, message: str = "Command error"):
        self.message = message
        super().__init__(self.message)

class CheckFailure(CommandError):
    """Raised when a command check fails."""
    def __init__(self, message: str = "Check failed"):
        super().__init__(message)

class CommandRegistrationError(CommandError):
    """Raised when there's an error registering a command or redeem."""
    def __init__(self, name: str, item_type: str = "Command"):
        self.name = name
        self.item_type = item_type
        super().__init__(f"{item_type} '{name}' already exists")

class ArgumentError(CommandError):
    """Base exception for argument parsing errors."""
    pass

class UnknownFlagError(ArgumentError):
    """Raised when an unknown flag is provided."""
    def __init__(self, flag_name: str):
        self.flag_name = flag_name
        super().__init__(f"Unknown flag '{flag_name}'")

class DuplicateParameterError(ArgumentError):
    """Raised when a parameter is provided multiple times."""
    def __init__(self, param_name: str):
        self.param_name = param_name
        super().__init__(f"Multiple values provided for parameter '{param_name}'")

class MissingRequiredArgumentError(ArgumentError):
    """Raised when a required argument is missing."""
    def __init__(self, param_name: str, keyword_only: bool = False):
        self.param_name = param_name
        self.keyword_only = keyword_only
        arg_type = "keyword-only argument" if keyword_only else "argument"
        super().__init__(f"Missing required {arg_type}: {param_name}")

class UnknownArgumentError(ArgumentError):
    """Raised when unknown arguments are provided."""
    def __init__(self, args: list):
        self.args = args
        args_str = ', '.join(f"'{arg}'" for arg in args) if isinstance(args[0], str) else ' '.join(str(a) for a in args)
        super().__init__(f"Unknown arguments: {args_str}")

class ArgumentConversionError(ArgumentError):
    """Raised when argument conversion fails."""
    def __init__(self, value: str, target_type: str, original_error: Exception = None):
        self.value = value
        self.target_type = target_type
        self.original_error = original_error
        error_msg = f"Could not convert '{value}' to {target_type}"
        if original_error:
            error_msg += f": {original_error}"
        super().__init__(error_msg)
        
class ArgumentParsingError(ArgumentError):
    """Raised when there is a general argument parsing error."""
    def __init__(self, message: str = "Error parsing arguments"):
        super().__init__(message)


@runtime_checkable
class Context(Protocol):
    @property
    def author(self) -> str:
        ...

    @property
    def command_name(self) -> str:
        ...

    @property
    def args(self) -> CommandArgs:
        ...

    @property
    def platform(self) -> str:
        ...

    @property
    def roles(self) -> List[UserRole]:
        ...
    
    @property
    def expected_output_type(self) -> OutputMessageType:
        ...

    @property
    def allows_markdown(self) -> bool:
        ...

    @property
    def source(self) -> Any:
        ...

    async def reply(self, text: str) -> None:
        ...

    async def send(self, text: str) -> None:
        ...

# Type for command functions - can be either sync or async
CommandFunc = Callable[..., Union[None, Awaitable[None]]]
RedeemFunc = Callable[['RedeemContext'], Union[None, Awaitable[None]]]
CheckFunc = Callable[[Context], bool]

class Commands:
    def __init__(self, chat: Chat, twitches: Dict[str, Twitch] = {}):
        self.chat: Chat = chat
        self.twitch: Twitch = chat.twitch
        self.twitches: Dict[str, Twitch] = twitches
        self.commands: Dict[str, Command] = {}
        self.redeems: Dict[str, Redeem] = {}
        self.prefix: str = "!"
        self.loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self.cog_manager: Optional[CogManager] = None
        self.converters: Dict[Type, Callable[[Context, str], Awaitable[Any]]] = {}

    def add_command_object(self, name: str, command: Command):
        cmd_name = name.lower()
        if cmd_name in self.commands:
            raise CommandRegistrationError(cmd_name, "Command")
            
        self.commands[cmd_name] = command

    def add_command(self, name: str, func: CommandFunc) -> Command:
        """Add a new command with the given name and handler function."""
        cmd_name = name.lower()
        if cmd_name in self.commands:
            raise CommandRegistrationError(cmd_name, "Command")
            
        cmd = Command(cmd_name, func)
        self.commands[cmd.name] = cmd
        return cmd

    def add_redeem_object(self, name: str, redeem: Redeem):
        redeem_name = name.lower()
        if redeem_name in self.redeems:
            raise CommandRegistrationError(redeem_name, "Redeem")
            
        self.redeems[redeem.name] = redeem

    def add_redeem(self, name: str, func: RedeemFunc) -> RedeemFunc:
        redeem_name = name.lower()
        if redeem_name in self.redeems:
            raise CommandRegistrationError(redeem_name, "Redeem")
            
        redeem = Redeem(redeem_name, func)
        self.redeems[redeem.name] = redeem
        return redeem
    
    def add_converter(self, type_: Type, converter: Callable[[Context, str], Awaitable[Any]]):
        self.converters[type_] = converter
        
    def setup_cog_manager(self) -> CogManager:
        if not hasattr(self, 'cog_manager') or self.cog_manager is None:
            from .cog import CogManager
            self.cog_manager = CogManager(self)
        return self.cog_manager
        
    def load_cog(self, cog_cls: type[Cog], **kwargs) -> None:
        if self.cog_manager is None:
            self.setup_cog_manager()
        try:
            self.cog_manager.load_cog(cog_cls, **kwargs)
        except Exception as e:
            logger.error(f"Error loading cog '{cog_cls.name}':", exc_info=True)
            raise
        
    def unload_cog(self, cog_name: str) -> None:
        if self.cog_manager:
            self.cog_manager.unload_cog(cog_name)
            
    def reload_cog(self, cog_cls: type[Cog], **kwargs) -> None:
        if self.cog_manager:
            self.cog_manager.reload_cog(cog_cls, **kwargs)

    async def get_prefix(self, msg: ChatMessage) -> str:
        return self.prefix
    
    async def on_command_error(self, ctx: Context, command: Command, error: Exception):
        # Handle argument parsing errors with user-friendly messages
        if isinstance(error, ArgumentError):
            logger.warning(f"Argument error for command {command.name}: {error.message}")
            await ctx.reply(f"❌ {error.message}")
            return
        
        if isinstance(error, CheckFailure):
            # Reply with custom error message if available
            logger.warning(f"Check failed for command {command.name}: {error.message}")
            await ctx.reply(error.message)
            return
        
        logger.error(f"Error executing command '{command.name}':", exc_info=True)
        await ctx.reply(f"An error occurred while executing the command: {error}")
    
    async def on_redeem_error(self, ctx: RedeemContext, redeem: Redeem, error: Exception):
        logger.error(f"Error executing redeem '{redeem.name}':", exc_info=True)
        
    def _find_command(self, text: str) -> tuple[str, str]:
        text_lower = text.lower()
        for cmd in sorted(self.commands.keys(), key=len, reverse=True):
            if text_lower == cmd or text_lower.startswith(cmd + ' '):
                return cmd, text[len(cmd):].strip()
        return None, text

    def _find_redeem(self, name: str) -> tuple[str, str]:
        name_lower = name.lower()
        for redeem in sorted(self.redeems.keys(), key=len, reverse=True):
            if name_lower == redeem or name_lower.startswith(redeem + ' '):
                return redeem, name[len(redeem):].strip()
        return None, name

    async def process_message(self, msg: ChatMessage) -> None:
        prefix = await self.get_prefix(msg)
        if not msg.text.startswith(prefix):
            return
            
        content = msg.text[len(prefix):]
        content = content.replace("\U000e0000", "").strip()
        
        command_name, remaining_text = self._find_command(content)
        if not command_name or command_name not in self.commands:
            return
            
        ctx = TwitchContext(msg, command_name, remaining_text, self)
        await self.commands[command_name].invoke(ctx)

    async def process_channel_point_redemption(self, redemption: ChannelPointsCustomRewardRedemptionData):
        redeem_name, remaining_text = self._find_redeem(redemption.reward.title)
        if not redeem_name or redeem_name not in self.redeems:
            return
            
        ctx = RedeemContext(redemption, self)
        try:
            result = self.redeems[redeem_name].func(ctx)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            await self.on_redeem_error(ctx, self.redeems[redeem_name], e)

class Command:
    def __init__(self, name: str, func: CommandFunc, **kwargs):
        self.name = name
        self.func = func
        self.aliases = kwargs.get('aliases', [])
        self.hidden = kwargs.get('hidden', False)
        self.checks: List[CheckFunc] = kwargs.get('checks', [])
        
        # Parse docstring
        doc = func.__doc__ or ""
        parsed_doc = parse_google_docstring(doc)
        self.help = kwargs.get('help', parsed_doc['summary'])
        self.description = parsed_doc['description']
        self.doc_args = parsed_doc['args']
        self.examples = parsed_doc['examples']
        self._arg_aliases = kwargs.get('arg_aliases', {})
        self.arg_mappings = {}
        for param_name, aliases in self._arg_aliases.items():
            if isinstance(aliases, str):
                aliases = [aliases]
            self.arg_mappings.update({alias: param_name for alias in aliases})
            self.arg_mappings[param_name] = param_name
        
        # Store signature and resolve type hints
        self.signature = inspect.signature(func)
        try:
            # get_type_hints resolves string annotations to actual types
            from typing import get_type_hints
            self.type_hints = get_type_hints(func)
        except Exception as e:
            # If get_type_hints fails, we'll fall back to the signature
            logger.warning(f"Could not resolve type hints for {func.__name__}: {e}")
            self.type_hints = {}

    async def _convert_argument(self, ctx: Context, value: str, param: inspect.Parameter) -> Any:
        if param.annotation == inspect.Parameter.empty:
            return value
            
        # Try to get the resolved type hint first
        type_ = self.type_hints.get(param.name, param.annotation)
        
        # If still a string (shouldn't happen with get_type_hints, but just in case)
        if isinstance(type_, str):
            # Try to evaluate common built-in types
            builtins_map = {
                'int': int,
                'float': float,
                'str': str,
                'bool': bool,
            }
            
            if type_ in builtins_map:
                type_ = builtins_map[type_]
            else:
                # For other forward references, just return the value
                return value
        
        # Handle Optional[T] - extract the inner type
        origin = get_origin(type_)
        if origin is Union:
            args = get_args(type_)
            # Filter out NoneType to get the actual type
            non_none_types = [t for t in args if t is not type(None)]
            if non_none_types:
                type_ = non_none_types[0]
        
        # Check for custom converter
        if hasattr(type_, 'convert') and inspect.iscoroutinefunction(type_.convert):
            try:
                return await type_.convert(ctx, value)
            except ArgumentError as e:
                raise ArgumentParsingError(f"Error parsing argument '{param.name}': {e.message}")
            except Exception as e:
                raise ArgumentParsingError(f"Error parsing argument '{param.name}'")
            
        if type_ in ctx.bot.converters:
            return await ctx.bot.converters[type_](ctx, value)
            
        if type_ is bool:
            if isinstance(value, bool):
                return value
            return value.lower() in ('true', 'yes', '1', 'on')
            
        try:
            return type_(value)
        except Exception as e:
            type_name = getattr(type_, '__name__', str(type_))
            raise ArgumentConversionError(value, type_name, e)

    async def _parse_arguments(self, ctx: Context) -> tuple[list, dict]:
        args = []
        kwargs = {}
        
        # Skip 'self' (if bound) and 'ctx'
        params = list(self.signature.parameters.values())
        if params and params[0].name == 'self':
            params.pop(0)
        if params and (params[0].name == 'ctx' or params[0].annotation == Context or params[0].annotation == 'Context'):
            params.pop(0)
            
        # Build a map of parameter names to their definitions
        param_map = {p.name: p for p in params}
        
        # Separate positional args and flags from ctx.args
        positional_args = []
        named_args = {}
        
        for item in ctx.args.args:
            if isinstance(item, Flag):
                if not item.name in self.arg_mappings:
                    raise UnknownFlagError(item.name)
                param_name = self.arg_mappings[item.name]
                if param_name in named_args:
                    raise DuplicateParameterError(param_name)
                named_args[param_name] = item.value
            else:
                positional_args.append(item)
        
        # Process parameters in order
        positional_index = 0
        
        for param in params:
            param_name = param.name
                        
            # Check if this was provided as a named argument (or alias)
            val = None
            if param_name in named_args:
                val = named_args[param_name]
                del named_args[param_name]
                break

            if val is not None:
                # Argument was provided by name
                converted = await self._convert_argument(ctx, val, param)
                kwargs[param_name] = converted
                continue
            
            # Handle VAR_POSITIONAL (*args)
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                for arg in positional_args[positional_index:]:
                    args.append(arg)
                positional_index = len(positional_args)
                break
            
            # Handle KEYWORD_ONLY parameters
            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                # Must be provided as named argument (already checked above)
                if param.default != inspect.Parameter.empty:
                    kwargs[param_name] = param.default
                elif self._is_optional(param):
                    kwargs[param_name] = None
                else:
                    raise MissingRequiredArgumentError(param_name, keyword_only=True)
                continue
            
            # Handle positional or positional-or-keyword parameters
            # (Named check already done above)
                
            # Otherwise, use positional argument
            if positional_index < len(positional_args):
                val = positional_args[positional_index]
                positional_index += 1
                
                # Check if this is the last parameter and it's a string (consume rest)
                if (positional_index == len(params) and 
                    param.annotation == str and 
                    positional_index < len(positional_args)):
                    # Consume remaining positional args as a single string
                    remaining = positional_args[positional_index:]
                    val = val + ' ' + ' '.join(remaining)
                    positional_index = len(positional_args)

                converted = await self._convert_argument(ctx, val, param)
                args.append(converted)
            else:
                # No positional argument provided
                if param.default != inspect.Parameter.empty:
                    args.append(param.default)
                elif self._is_optional(param):
                    args.append(None)
                else:
                    raise MissingRequiredArgumentError(param_name)

        if len(named_args) > 0:
            raise UnknownArgumentError(list(named_args.keys()))
        if positional_index < len(positional_args):
            raise UnknownArgumentError(positional_args[positional_index:])

        return args, kwargs
    
    def _is_optional(self, param: inspect.Parameter) -> bool:
        """Check if a parameter is Optional[T]."""
        origin = get_origin(param.annotation)
        if origin is Union:
            args = get_args(param.annotation)
            return type(None) in args
        return False

    async def invoke(self, ctx: Context) -> None:
        """Invoke the command with the given context."""
        try:
            # Run checks
            for check_func in self.checks:
                # The check function can either return bool or an error string
                try:
                    check_result = check_func(ctx)
                    if isinstance(check_result, str):
                        raise CheckFailure(check_result)
                    if not check_result:
                        raise CheckFailure(f"Check failed for command '{self.name}'")
                except CheckFailure:
                    raise
                except Exception as e:
                    # Wrap other exceptions
                    raise CheckFailure(f"Check raised an error: {e}")

            # Parse arguments
            args, kwargs = await self._parse_arguments(ctx)
            
            # Call the function
            result = self.func(ctx, *args, **kwargs)
            
            # Await the result if it's a coroutine
            if asyncio.iscoroutine(result):
                await result
                
        except Exception as e:
            if hasattr(ctx, 'bot') and hasattr(ctx.bot, 'on_command_error'):
                await ctx.bot.on_command_error(ctx, self, e)
            logger.error("Error in command invocation:", exc_info=True)

class Redeem:
    def __init__(self, name: str, func: RedeemFunc, **kwargs):
        self.name = name
        self.func = func
        
    async def invoke(self, ctx: RedeemContext, redemption: ChannelPointsCustomRewardRedemptionData) -> None:
        try:
            func = self.func
            result = func(ctx, redemption) # type: ignore
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            if hasattr(ctx, 'bot') and hasattr(ctx.bot, 'on_redeem_error'):
                await ctx.bot.on_redeem_error(ctx, self, e)
            else:
                logger.error("Error in redeem invocation:", exc_info=True)

class TwitchContext(Context):
    def __init__(self, msg: ChatMessage, command: str, parameter: str, bot: Commands):
        self.msg: ChatMessage = msg
        self._command: str = command
        self._parameter: str = parameter
        self._args: CommandArgs = CommandArgs(parameter)
        self.bot: Commands = bot
        self.twitch: Twitch = bot.twitches.get(msg.room.room_id)

    @property
    def author(self) -> str:
        return self.msg.user.name

    @property
    def command_name(self) -> str:
        return self._command

    @property
    def args(self) -> CommandArgs:
        return self._args

    @property
    def platform(self) -> str:
        return "twitch"
    
    @property
    def source(self) -> ChatMessage:
        return self.msg

    @property
    def expected_output_type(self) -> OutputMessageType:
        return OutputMessageType.SINGLE_LINE

    @property
    def allows_markdown(self) -> bool:
        return False

    @property
    def roles(self) -> List[UserRole]:
        roles = [UserRole.USER]
        if self.msg.user.mod or self.msg.user.name == self.msg.room.name:
            roles.append(UserRole.MODERATOR)
        if self.msg.user.subscriber:
            roles.append(UserRole.SUBSCRIBER)
        
        owner_username = os.getenv("OWNER_TWITCH_USERNAME")
        if owner_username and self.msg.user.name.lower() == owner_username.lower():
            roles.append(UserRole.BOT_OWNER)
            roles.append(UserRole.ADMIN)
            
        return roles

    async def reply(self, text: str):
        await self.msg.reply(text)

    async def send(self, text: str):
        await self.msg.chat.send_message(self.msg.room.name, text)

class RedeemContext:
    def __init__(self, redemption: ChannelPointsCustomRewardRedemptionData, bot: Commands):
        self.twitch: Twitch = bot.twitches.get(redemption.broadcaster_user_id)
        self.redemption: ChannelPointsCustomRewardRedemptionData = redemption
        self.bot: Commands = bot

    async def update_status(self, status: CustomRewardRedemptionStatus):
        if self.redemption.status == "unfulfilled":
            await self.twitch.update_redemption_status(
                self.redemption.broadcaster_user_id,
                self.redemption.reward.id,
                self.redemption.id,
                status
            )
        else:
            logger.warning(f"Redemption is not in the UNFULFILLED state (current: {self.redemption.status})", stack_info=True)
    
    async def fulfill(self):
        await self.update_status(CustomRewardRedemptionStatus.FULFILLED)
    
    async def cancel(self):
        await self.update_status(CustomRewardRedemptionStatus.CANCELED)

    async def send(self, text: str):
        await self.bot.chat.send_message(self.redemption.broadcaster_user_login, text)

class CustomRewardRedemptionStatus(Enum):
    UNFULFILLED = 'UNFULFILLED'
    FULFILLED = 'FULFILLED'
    CANCELED = 'CANCELED'

@dataclass
class Flag:
    name: str
    value: str = None

    def __repr__(self):
        return f"Flag({self.name}, {self.value})"

# Helper for checks
def check(predicate: CheckFunc):
    """Decorator to add a check to a command.
    
    Args:
        predicate: A function that takes a Context and returns bool or raises CheckFailure.
    """
    def decorator(func):
        if not hasattr(func, '_command_checks'):
            func._command_checks = []
        func._command_checks.append(predicate)
        return func
    return decorator

DELIMETERS = ('=', ':')
RE_FLAG = re.compile(r'[-a-zA-Z]{2}[a-zA-Z]+[:=]+.+|-[a-zA-Z]\b|--[a-zA-Z]+\b')

class CommandArgs:
    def __init__(self, text: str):
        self.text = text
        
        self.args: List[str | Flag] = []  # args are in order of appearance
        self.flags: List[Flag] = []  # flags are in order of appearance
        self.grouped_args: List[str] = []  # consecutive non-flag args joined by space
        self._parse()

    def _parse(self):
        if not self.text.strip():
            return
        
        in_quotes = None  # None if not in quotes, otherwise the quote char (' or ")
        current = []
        args: list[str] = []
        i = 0
        n = len(self.text)
        
        while i < n:
            char = self.text[i]
            
            # Handle quotes
            if char in ('"', "'"):
                if i > 0 and self.text[i-1] == '\\':
                    # Escaped quote, add to current and remove the backslash
                    current[-1] = char
                elif in_quotes is None:
                    # Start of quoted string
                    # current.append('"')
                    in_quotes = char
                elif char == in_quotes:
                    # End of quoted string
                    # current.append('"')
                    in_quotes = None
                else:
                    # Nested quotes of different type, add to current
                    current.append(char)
            elif char.isspace() and in_quotes is None:
                if current:
                    args.append(''.join(current))
                    current = []
            else:
                current.append(char)
                
            i += 1
                
        if current:
            args.append(''.join(current))
        
        for arg in args:
            delimiter_char = None
            has_delimiter = False
            for delimiter in DELIMETERS:
                if delimiter in arg:
                    has_delimiter = True
                    delimiter_char = delimiter
                    break
            is_quoted = arg[0] == '"' and arg[-1] == '"'
            if RE_FLAG.match(arg):
                flag_name: str = arg.lstrip('-')
                flag_value: str | None = True
                if has_delimiter:
                    if delimiter_char in flag_name:
                        flag_name, flag_value = flag_name.split(delimiter_char, 1)
                flag = Flag(flag_name, flag_value)
                self.flags.append(flag)
                self.args.append(flag)
            else:
                if is_quoted:
                    arg = arg[1:-1]
                self.args.append(arg)

        # Build grouped_args by joining consecutive non-flag args with spaces,
        # using flags as separators (flags are not included in grouped_args)
        grouped: list[str] = []
        current_group: list[str] = []
        for item in self.args:
            if isinstance(item, Flag):
                if current_group:
                    grouped.append(' '.join(current_group))
                    current_group = []
            else:
                current_group.append(item)
        if current_group:
            grouped.append(' '.join(current_group))
        self.grouped_args = grouped

    def get_flag(self, name: str | list[str], case_sensitive: bool = False, default: str | None = None) -> Flag | None:
        names = name if isinstance(name, list) else [name]
        for flag in self.flags:
            if case_sensitive and flag.name in names:
                return flag
            elif not case_sensitive and flag.name.lower() in [n.lower() for n in names]:
                return flag
        return Flag(name, default)

# Alias for backward compatibility
CommandContext = TwitchContext