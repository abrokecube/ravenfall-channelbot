from __future__ import annotations

import asyncio
import logging
import inspect
import os
from typing import (
    Callable, 
    List,
    Dict, 
    Awaitable, 
    Union, 
    Optional, 
    TYPE_CHECKING, 
    Protocol, 
    runtime_checkable, 
    Any, 
    Type, 
    cast, 
    get_origin, 
    get_args
)
from twitchAPI.twitch import Twitch
from twitchAPI.chat import ChatMessage, Chat
from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionData
from dataclasses import dataclass, field
import re
from docstring_parser import parse
from utils.strutils import strjoin

from .command_contexts import Context, TwitchContext
from .command_enums import OutputMessageType, Platform, UserRole, CustomRewardRedemptionStatus
from .command_exceptions import (
    CommandError,
    CheckFailure,
    CommandRegistrationError,
    ArgumentError,
    UnknownFlagError,
    DuplicateParameterError,
    MissingRequiredArgumentError,
    UnknownArgumentError,
    ArgumentConversionError,
    ArgumentParsingError
)

# Configure logger for this module
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .cog import Cog, CogManager

# Type for command functions - can be either sync or async
CommandFunc = Callable[..., Union[None, Awaitable[None]]]
TwitchRedeemFunc = Callable[['TwitchRedeemContext'], Union[None, Awaitable[None]]]
CheckFunc = Callable[[Context], bool]


@dataclass
class Parameter:
    name: str
    annotation: Any
    default: Any = inspect.Parameter.empty
    aliases: List[str] = field(default_factory=list)
    greedy: bool = False
    hidden: bool = False
    kind: inspect._ParameterKind = inspect.Parameter.POSITIONAL_OR_KEYWORD
    converter: Any = field(default=None)
    is_optional: bool = False
    type_title: str = None
    type_short_help: str = None
    type_help: str = None
    help: Optional[str] = None
    
    def get_help_text(self, invoked_name: str = None):
        param_aliases = self.aliases[:]
        
        if invoked_name in param_aliases:
            param_aliases.remove(invoked_name)
            param_aliases.append(self.name)
        param_aliases.sort()

        out_str = []
        param_str = invoked_name or self.name
        if self.type_title:
            param_str += f": {self.type_title}"
        if self.is_optional:
            param_str = f"({param_str})"
        else:
            param_str = f"<{param_str}>"
        out_str.append(param_str)
        out_str.append(self.help)
        if self.is_optional:
            out_str.append("Optional")
        else:
            out_str.append("Required")
        type_help = self.type_short_help or self.type_help or None
        if type_help:
            out_str.append(f"{self.type_title}: {type_help}")
        if param_aliases:
            out_str.append(f"Aliases: {', '.join(param_aliases)}")
            
        response = strjoin(' – ', *out_str)
        return response


class Converter:
    title: str = None
    short_help: str = None
    help: str = None

    @classmethod
    async def convert(cls, ctx: Context, arg: str) -> Any:
        raise NotImplementedError


class Check:
    title: str = None
    short_help: str = None
    help: str = None

    async def check(self, ctx: Context) -> bool:
        raise NotImplementedError

    def __call__(self, ctx: Context) -> bool:
        # This allows the check instance to be called like a function
        # We need to handle async check methods
        if inspect.iscoroutinefunction(self.check):
            # If check is async, we can't await it here because __call__ is sync
            # But the command invoker expects a sync callable that might return a coroutine?
            # No, the command invoker calls check_func(ctx). 
            # If check_func is a Check instance, check_func(ctx) returns self.check(ctx)
            # which is a coroutine if check is async.
            return self.check(ctx)
        return self.check(ctx)

class FunctionCheck(Check):
    def __init__(self, predicate: CheckFunc):
        self.predicate = predicate
        self.title = predicate.__name__.replace('_', ' ').title()
        self.help = getattr(predicate, '__doc__', '')
    
    def check(self, ctx: Context) -> bool:
        return self.predicate(ctx)


class Commands:
    def __init__(self, chat: Chat, twitches: Dict[str, Twitch] = {}):
        self.chat: Chat = chat
        self.twitch: Twitch = chat.twitch
        self.twitches: Dict[str, Twitch] = twitches
        self.commands: Dict[str, Command] = {}
        self.redeems: Dict[str, TwitchRedeem] = {}
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

    def add_redeem_object(self, name: str, redeem: TwitchRedeem):
        redeem_name = name.lower()
        if redeem_name in self.redeems:
            raise CommandRegistrationError(redeem_name, "Redeem")
            
        self.redeems[redeem.name] = redeem

    def add_redeem(self, name: str, func: TwitchRedeemFunc) -> TwitchRedeemFunc:
        redeem_name = name.lower()
        if redeem_name in self.redeems:
            raise CommandRegistrationError(redeem_name, "Redeem")
            
        redeem = TwitchRedeem(redeem_name, func)
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

    async def get_prefix(self, ctx: Context) -> str:
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
    
    async def on_redeem_error(self, ctx: TwitchRedeemContext, redeem: TwitchRedeem, error: Exception):
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

    async def process_twitch_message(self, msg: ChatMessage) -> None:
        await self.process_message(Platform.TWITCH, msg)

    async def process_message(self, platform: Platform, platform_context: Any):
        ctx: Context = None
        if platform == Platform.TWITCH:
            ctx = TwitchContext(platform_context)
        else:
            raise ValueError(f"Unsupported platform: {platform}")
        
        prefix = await self.get_prefix(ctx)
        used_prefix = ""
        if isinstance(prefix, list):
            for p in prefix:
                if ctx.message.startswith(p):
                    used_prefix = p
                    break
            else:
                return
        else:
            if not ctx.message.startswith(prefix):
                return
            used_prefix = prefix
            
        content = ctx.message[len(used_prefix):]
        content = content.replace("\U000e0000", "").strip()
        
        command_name, remaining_text = self._find_command(content)
        if not command_name or command_name not in self.commands:
            return

        command = self.commands[command_name]

        ctx.command = command
        ctx.prefix = used_prefix
        ctx.invoked_with = content[:len(command_name)]
        ctx.parameters = remaining_text

        await command.invoke(ctx)

    async def process_channel_point_redemption(self, redemption: ChannelPointsCustomRewardRedemptionData):
        redeem_name, remaining_text = self._find_redeem(redemption.reward.title)
        if not redeem_name or redeem_name not in self.redeems:
            return
            
        ctx = TwitchRedeemContext(redemption, self)
        try:
            result = self.redeems[redeem_name].func(ctx)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            await self.on_redeem_error(ctx, self.redeems[redeem_name], e)


class Command:       
    def __init__(
        self, name: str, func: CommandFunc, bot: 'Commands', checks: List[CheckFunc] = [], 
        aliases: List[str] = [], hidden: bool = False, help: str = None, short_help: str = None, 
        title: str = None
    ):
        self.name = name
        self.func = func
        self.bot = bot
        self.checks = checks
        self.aliases = aliases
        self.hidden = hidden
        
        # Parse docstring for parameter descriptions and help text
        doc = parse(func.__doc__ or "")
        
        self.title = title or name.replace('_', ' ').title()
        self.short_help = short_help or doc.short_description
        self.help = help or doc.long_description or doc.short_description
        
        doc_params = {p.arg_name: p.description for p in doc.params}

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

        # Load parameter configurations from decorator
        params_config = getattr(func, '_command_params', {})

        self.parameters: List[Parameter] = []
        self.parameters_map: Dict[str, Parameter] = {}
        self.arg_mappings: Dict[str, str] = {}
        
        # Process parameters
        # Skip 'self' (if bound) and 'ctx'
        sig_params = list(self.signature.parameters.values())
        if sig_params and sig_params[0].name == 'self':
            sig_params.pop(0)
        if sig_params and (sig_params[0].name == 'ctx' or sig_params[0].annotation == Context or sig_params[0].annotation == 'Context'):
            sig_params.pop(0)

        for param in sig_params:
            param_config = params_config.get(param.name, {})
            
            # Resolve aliases
            aliases = param_config.get('aliases', [])
            if isinstance(aliases, str):
                aliases = [aliases]
            
            # Resolve type and check for Optional
            annotation = self.type_hints.get(param.name, param.annotation)
            converter = annotation
            is_optional = False
            
            # If still a string (shouldn't happen with get_type_hints, but just in case)
            if isinstance(converter, str):
                # Try to evaluate common built-in types
                builtins_map = {
                    'int': int,
                    'float': float,
                    'str': str,
                    'bool': bool,
                }
                if converter in builtins_map:
                    converter = builtins_map[converter]

            # Handle Optional[T] - extract the inner type
            origin = get_origin(converter)
            if origin is Union:
                args = get_args(converter)
                # Check if NoneType is in args
                if type(None) in args:
                    is_optional = True
                    # Filter out NoneType to get the actual type
                    non_none_types = [t for t in args if t is not type(None)]
                    if non_none_types:
                        converter = non_none_types[0]
            
            is_optional = is_optional or (param.default != inspect.Parameter.empty)

            # Get help text
            param_help = param_config.get('help')
            if not param_help:
                param_help = doc_params.get(param.name)

            # Create Parameter object
            p = Parameter(
                name=param.name,
                annotation=annotation,
                converter=converter,
                is_optional=is_optional,
                default=param.default,
                aliases=aliases,
                greedy=param_config.get('greedy', False),
                hidden=param_config.get('hidden', False),
                kind=param.kind,
                help=param_help
            )
            
            # Extract documentation from converter if available
            if isinstance(converter, type) and issubclass(converter, Converter):
                p.type_title = getattr(converter, 'title', None) or converter.__name__
                p.type_short_help = getattr(converter, 'short_help', None)
                p.type_help = getattr(converter, 'help', None) or converter.__doc__
            elif converter in BUILTIN_TYPE_DOCS:
                docs = BUILTIN_TYPE_DOCS[converter]
                p.type_title = docs['title']
                p.type_short_help = docs['short_help']
                p.type_help = docs['help']
            
            self.parameters.append(p)
            self.parameters_map[param.name] = p
            
            # Update mappings
            self.arg_mappings[param.name] = param.name
            for alias in aliases:
                self.arg_mappings[alias] = param.name

        # Add command-level arg aliases (legacy support if needed, or remove if fully deprecated)
        # _arg_aliases = kwargs.get('arg_aliases', {}) # kwargs is no longer used
        # for param_name, aliases in _arg_aliases.items():
        #     if isinstance(aliases, str):
        #         aliases = [aliases]
        #     for alias in aliases:
        #         self.arg_mappings[alias] = param_name

    async def _convert_argument(self, ctx: Context, value: str, param: Parameter) -> Any:
        if param.annotation == inspect.Parameter.empty:
            return value
            
        type_ = param.converter
        
        # Check for custom converter
        if hasattr(type_, 'convert') and inspect.iscoroutinefunction(type_.convert):
            try:
                return await type_.convert(ctx, value)
            except ArgumentError as e:
                raise ArgumentParsingError(f"Error parsing argument '{param.name}': {e.message}")
            except Exception as e:
                raise ArgumentParsingError(f"Error parsing argument '{param.name}'")
            
        if type_ in self.bot.converters:
            return await self.bot.converters[type_](ctx, value)
            
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
        
        # Separate positional args and flags from ctx.args
        positional_args = []
        named_args = {}
        parsed_args = CommandArgs(ctx.parameters)

        for item in parsed_args.args:
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
        
        for param in self.parameters:
            param_name = param.name
                        
            # Check if this was provided as a named argument (or alias)
            val = None
            if param_name in named_args:
                val = named_args[param_name]
                del named_args[param_name]

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
            
            # Handle KEYWORD_ONLY parameters
            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                # Must be provided as named argument (already checked above)
                if param.default != inspect.Parameter.empty:
                    kwargs[param_name] = param.default
                elif param.is_optional:
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
                # OR if it is marked as greedy
                
                # Check if this is the last parameter in the list
                is_last_param = param is self.parameters[-1]

                if ((is_last_param and param.annotation == str) or param.greedy) and positional_index < len(positional_args):
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
                elif param.is_optional:
                    args.append(None)
                else:
                    raise MissingRequiredArgumentError(param_name)

        if len(named_args) > 0:
            raise UnknownArgumentError(list(named_args.keys()))
        if positional_index < len(positional_args):
            raise UnknownArgumentError(positional_args[positional_index:])

        return args, kwargs


    async def invoke(self, ctx: Context) -> None:
        """Invoke the command with the given context."""
        try:
            # Run checks
            for check_func in self.checks:
                # The check function can either return bool or an error string
                try:
                    check_result = check_func(ctx)
                    if asyncio.iscoroutine(check_result):
                        check_result = await check_result
                    
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
            if hasattr(self.bot, 'on_command_error'):
                await self.bot.on_command_error(ctx, self, e)
            logger.error("Error in command invocation:", exc_info=True)
            
    def get_help_text(self, prefix: str, invoked_name: str = None):
        if not invoked_name:
            invoked_name = self.name
        nm_out = [f"{prefix}{invoked_name}"]
        description = self.short_help or self.help or ""
        
        for param in self.parameters:
            if param.hidden:
                continue
            param_str = param.name
            if param.type_title:
                param_str += f": {param.type_title}"
            if param.is_optional:
                param_str = f"({param_str})"
            else:
                param_str = f"<{param_str}>"
            nm_out.append(param_str)
        
        name_and_usage = " ".join(nm_out)
        aliases = ""
        if self.aliases:
            alias_list = list(self.aliases)
            if invoked_name != self.name:
                alias_list.remove(invoked_name)
                alias_list.append(self.name)
            alias_list.sort()
            aliases = f"Aliases: {', '.join(alias_list)}"
        restrictions = ""
        if self.checks:
            restr_to = []
            for check in self.checks:
                if check.__doc__:
                    restr_to.append(check.__doc__)
                else:
                    restr_to.append(check.__name__)
            restrictions = f"Limited to: {', '.join(restr_to)}"

        response = strjoin(' – ', name_and_usage, description, restrictions, aliases)
        return response


class TwitchRedeem:
    def __init__(self, name: str, func: TwitchRedeemFunc, bot: Commands, **kwargs):
        self.name = name
        self.func = func
        self.bot = bot
        
    async def invoke(self, ctx: TwitchRedeemContext, redemption: ChannelPointsCustomRewardRedemptionData) -> None:
        try:
            func = self.func
            result = func(ctx, redemption) # type: ignore
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            if hasattr(ctx, 'bot') and hasattr(self.bot, 'on_redeem_error'):
                await self.bot.on_redeem_error(ctx, self, e)
            else:
                logger.error("Error in redeem invocation:", exc_info=True)

class TwitchRedeemContext:
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


@dataclass
class Flag:
    name: str
    value: str = None

    def __repr__(self):
        return f"Flag({self.name}, {self.value})"

# Helper for checks
def checks(*predicates: Union[CheckFunc, Check, Type[Check]]):
    """Decorator to add checks to a command.
    
    Args:
        *predicates: One or more functions or Check classes/instances.
    """
    def decorator(func):
        if not hasattr(func, '_command_checks'):
            func._command_checks = []
        
        processed_checks = []
        for p in predicates:
            if isinstance(p, type) and issubclass(p, Check):
                processed_checks.append(p())
            elif isinstance(p, Check):
                processed_checks.append(p)
            else:
                processed_checks.append(FunctionCheck(p))
                
        func._command_checks.extend(processed_checks)
        return func
    return decorator

def parameter(name: str, aliases: Union[str, List[str]] = [], greedy: bool = False, hidden: bool = False, help: str = None):
    """Decorator to configure a command parameter.
    
    Args:
        name: The name of the parameter to configure.
        aliases: Optional alias or list of aliases for the parameter.
        greedy: If True, the parameter will consume all remaining input as a single string.
        hidden: If True, the parameter will be hidden from help documentation.
        help: Help text for the parameter.
    """
    def decorator(func):
        if not hasattr(func, '_command_params'):
            func._command_params = {}
        func._command_params[name] = {
            'aliases': aliases,
            'greedy': greedy,
            'hidden': hidden,
            'help': help
        }
        return func
    return decorator

BUILTIN_TYPE_DOCS = {
    str: {
        'title': 'Text',
        'short_help': 'A text string',
        'help': 'A sequence of characters.'
    },
    int: {
        'title': 'Number',
        'short_help': 'An integer number',
        'help': 'A whole number without decimals.'
    },
    float: {
        'title': 'Decimal',
        'short_help': 'A decimal number',
        'help': 'A number with a decimal point.'
    },
    bool: {
        'title': 'Boolean',
        'short_help': 'True or False',
        'help': 'A boolean value (true/false, yes/no, on/off).'
    }
}

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