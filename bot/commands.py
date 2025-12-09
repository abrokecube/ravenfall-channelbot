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
from utils.format_time import format_seconds, TimeSize

from .command_contexts import Context, TwitchContext, TwitchRedeemContext
from .command_enums import OutputMessageType, Platform, UserRole, CustomRewardRedemptionStatus, ParameterKind, BucketType
from .command_exceptions import (
    CommandError,
    CheckFailure,
    VerificationFailure,
    CommandOnCooldown,
    CommandRegistrationError,
    ArgumentError,
    UnknownFlagError,
    DuplicateParameterError,
    MissingRequiredArgumentError,
    UnknownArgumentError,
    ArgumentConversionError,
    EmptyFlagValueError
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
    display_name: str
    annotation: Any
    default: Any = inspect.Parameter.empty
    aliases: List[str] = field(default_factory=list)
    greedy: bool = False
    hidden: bool = False
    kind: ParameterKind = ParameterKind.POSITIONAL_OR_KEYWORD
    converter: Any = field(default=None)
    is_optional: bool = False
    type_title: str = None
    type_short_help: str = None
    type_help: str = None
    help: Optional[str] = None
    command: 'Command' = None
    regex: str = None
    
    def get_parameter_display(self, invoked_name: str = None) -> str:
        param_str = invoked_name or self.display_name
        if self.type_title:
            param_str += f": {self.type_title}"
        if self.kind == ParameterKind.KEYWORD_ONLY:
            if len(param_str) == 1:
                param_str = f"(-{param_str})"
            else:
                param_str = f"(--{param_str})"
        elif self.is_optional:
            param_str = f"({param_str})"
        else:
            param_str = f"<{param_str}>"
        return param_str
    
    def get_help_text(self, invoked_name: str = None):
        param_aliases = self.aliases[:]
        
        if invoked_name in param_aliases:
            param_aliases.remove(invoked_name)
            param_aliases.append(self.name)
        if self.display_name != self.name and invoked_name == self.name:
            param_aliases.append(self.display_name)
        param_aliases.sort()

        out_str = []
        param_str = self.get_parameter_display(invoked_name)
        out_str.append(param_str)
        help_text = self.help
        type_help = self.type_short_help or self.type_help or None            
        if not help_text:
            if self.kind == ParameterKind.VAR_KEYWORD:
                help_text = "Command accepts any named argument"
            elif self.kind == ParameterKind.VAR_POSITIONAL:
                help_text = "Command accepts any additional arguments"
            elif type_help:
                help_text = type_help
                type_help = None
        out_str.append(help_text)
        properties = []
        if self.is_optional:
            properties.append("optional")
        else:
            properties.append("required")
        if self.kind == ParameterKind.KEYWORD_ONLY:
            properties.append("keyword-only")
        out_str.append(f"{', '.join(properties)}".capitalize())
        if type_help:
            out_str.append(f"Expects {self.type_title}: {type_help}")
        if param_aliases:
            out_str.append(f"Aliases: {', '.join(param_aliases)}")
            
        response = strjoin(' – ', *out_str)
        return response


class Converter:
    """To display a custom error message when conversion fails,
    raise command_exceptions.ArgumentConversionError in the convert method."""
    title: str = None
    short_help: str = None
    help: str = None

    @classmethod
    async def convert(cls, ctx: Context, arg: str) -> Any:
        raise NotImplementedError


class Cooldown:
    def __init__(self, rate: int, per: float, bucket: Union[BucketType, List[BucketType]] = BucketType.DEFAULT):
        self.rate = rate
        self.per = per

        if not isinstance(bucket, list):
            bucket = [bucket]
        self.bucket = bucket
        self._windows: Dict[Any, List[float]] = {}
    
    def _get_bucket_key(self, ctx: Context) -> str:
        keys = [str(ctx.get_bucket_key(t)) for t in self.bucket]
        return ":".join(keys)

    def get_retry_after(self, ctx: Context) -> float:
        import time
        now = time.time()
        key = self._get_bucket_key(ctx)
        
        if key not in self._windows:
            return 0.0
            
        window = self._windows[key]
        # Remove expired timestamps
        window = [t for t in window if now - t < self.per]
        self._windows[key] = window
        
        if len(window) < self.rate:
            return 0.0
            
        return self.per - (now - window[0])

    def update_rate_limit(self, ctx: Context):
        import time
        now = time.time()
        key = self._get_bucket_key(ctx)
        
        if key not in self._windows:
            self._windows[key] = []
            
        window = self._windows[key]
        # Remove expired timestamps
        window = [t for t in window if now - t < self.per]
        window.append(now)
        self._windows[key] = window


class BaseCommand:
    def __init__(self, name: str, checks: List[Check] = None, cooldown: Cooldown = None, verifier: Callable = None, aliases: List[str] = []):
        self.name = name
        self.checks = checks or []
        self.cooldown = cooldown
        self.verifier = verifier
        self.aliases = aliases

    async def _run_checks(self, ctx: Context):
        for check in self.checks:
            try:
                check_result = check.check(ctx)
                if asyncio.iscoroutine(check_result):
                    check_result = await check_result
                
                if isinstance(check_result, str):
                    raise CheckFailure(check_result)
                if not check_result:
                    raise CheckFailure(f"Check failed for command '{self.name}'")
            except CheckFailure:
                raise
            except Exception as e:
                raise CheckFailure(f"Check raised an error: {e}")
        return True

    async def _check_cooldown(self, ctx: Context):
        if self.cooldown:
            retry_after = self.cooldown.get_retry_after(ctx)
            if retry_after > 0:
                raise CommandOnCooldown(self.cooldown, retry_after)
            self.cooldown.update_rate_limit(ctx)
        return True

    async def _run_verification(self, ctx: Context, *args, **kwargs):
        # Run verifier if present
        if self.verifier:
            try:
                verify_result = self.verifier(ctx, *args, **kwargs)
                if asyncio.iscoroutine(verify_result):
                    verify_result = await verify_result
                
                if isinstance(verify_result, str):
                    raise VerificationFailure(verify_result)
                if verify_result is False:
                    raise VerificationFailure(f"Verification failed for command '{self.name}'")
            except VerificationFailure:
                raise
            except Exception as e:
                raise VerificationFailure("There was an error during verification")
        return True

    async def invoke(self, ctx: Context, *args, **kwargs):
        raise NotImplementedError


class EventListener(BaseCommand):
    def __init__(self, name: str, func: Callable, event_type: str, checks: List[Check] = None, cooldown: Cooldown = None, verifier: Callable = None, aliases: List[str] = []):
        super().__init__(name, checks, cooldown, verifier, aliases)
        self.func = func
        self.event_type = event_type
        
        # Load cooldown/verifier from decorator if not provided
        if not self.cooldown:
            self.cooldown = getattr(func, '_command_cooldown', None)
        if not self.verifier:
            self.verifier = getattr(func, '_command_verifier', None)
            
        # Load checks from decorator
        if hasattr(func, '_command_checks'):
            self.checks.extend(getattr(func, '_command_checks'))

        # Parse docstring for parameter descriptions and help text
        doc = parse(func.__doc__ or "")
        
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
        params_config: Dict = getattr(func, '_command_params', {})
        
        kind_mapping = {
            inspect.Parameter.POSITIONAL_ONLY: ParameterKind.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD: ParameterKind.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL: ParameterKind.VAR_POSITIONAL,
            inspect.Parameter.KEYWORD_ONLY: ParameterKind.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD: ParameterKind.VAR_KEYWORD
        }

        self.parameters: List[Parameter] = []
        self.parameters_map: Dict[str, Parameter] = {}
        self.arg_mappings: Dict[str, str] = {}
        
        doc_params = {p.arg_name: p.description for p in doc.params}

        # Process parameters
        # Skip 'self' (if bound) and 'ctx'
        sig_params = list(self.signature.parameters.values())
        if sig_params and sig_params[0].name == 'self':
            sig_params.pop(0)
        if sig_params and (sig_params[0].name == 'ctx' or sig_params[0].annotation == Context or sig_params[0].annotation == 'Context' or 'Context' in str(sig_params[0].annotation)):
            sig_params.pop(0)
            
        # Also skip 'redemption' if it's a redeem event and explicitly typed or named
        if self.event_type == "twitch_redeem" and sig_params:
             if sig_params[0].name == 'redemption' or 'ChannelPointsCustomRewardRedemptionData' in str(sig_params[0].annotation):
                 sig_params.pop(0)

        for param in sig_params:
            param_config: Dict = params_config.get(param.name, {})
            
            # Resolve aliases
            aliases = param_config.get('aliases', [])
            if isinstance(aliases, str):
                aliases = [aliases]
            
            display_name = param_config.get('display_name', None) or param.name

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
            
            is_optional = is_optional or \
                (param.default != inspect.Parameter.empty) or \
                (param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD))

            # Get help text
            param_help = param_config.get('help')
            if not param_help:
                param_help = doc_params.get(param.name)
                
            converter = param_config.get('converter', None) or converter
            if not converter:
                converter = str

            # Create Parameter object
            p = Parameter(
                name=param.name,
                display_name=display_name,
                annotation=annotation,
                converter=converter,
                is_optional=is_optional,
                default=param.default,
                aliases=aliases,
                greedy=param_config.get('greedy', False),
                hidden=param_config.get('hidden', False),
                kind=kind_mapping[param.kind],
                help=param_help,
                command=self,
                regex=param_config.get('regex')
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
            self.arg_mappings[display_name] = param.name
            for alias in aliases:
                self.arg_mappings[alias] = param.name

    async def _convert_argument(self, ctx: Context, value: str, param: Parameter) -> Any:
        if value is None:
            return value
        
        if param.annotation == inspect.Parameter.empty:
            return value
            
        type_ = param.converter
        
        # Check for custom converter
        if hasattr(type_, 'convert') and inspect.iscoroutinefunction(type_.convert):
            if value == True:
                raise EmptyFlagValueError(param)
            try:
                return await type_.convert(ctx, value)
            except ArgumentConversionError as e:
                raise ArgumentConversionError(e.message, value, param)
            except Exception as e:
                raise ArgumentConversionError(None, value, param, e)
                        
        if type_ is bool:
            if isinstance(value, bool):
                return value
            return value.lower() in ('true', 'yes', '1', 'on')
        elif type_ is int:
            try:
                return int(value)
            except ValueError as e:
                raise ArgumentConversionError("Expected an integer", value, param, e)
        elif type_ is float:
            try:
                return float(value)
            except ValueError as e:
                raise ArgumentConversionError("Expected a number", value, param, e)
        elif type_ is str:
            if value == True:
                raise EmptyFlagValueError(param)
            return value
        else:
            # Attempt to call the type as a constructor
            try:
                return type_(value)
            except Exception as e:
                raise ArgumentConversionError(f"Could not convert to {type_.__name__}", value, param, e)    
        
    async def _parse_arguments(self, ctx: Context) -> tuple[list, dict]:
        args = []
        kwargs = {}
        
        # Separate positional args and flags from ctx.args
        positional_args = []
        named_args = {}
        parsed_args = CommandArgs(ctx.parameters)

        # Check if we have a VAR_KEYWORD parameter
        has_var_keyword = any(p.kind == ParameterKind.VAR_KEYWORD for p in self.parameters)

        for item in parsed_args.args:
            if isinstance(item, Flag):
                if item.name in self.arg_mappings:
                    param_name = self.arg_mappings[item.name]
                elif has_var_keyword:
                    param_name = item.name
                else:
                    raise UnknownFlagError(item.name)
                
                if param_name in named_args:
                    if param_name in self.parameters_map:
                        raise DuplicateParameterError(self.parameters_map[param_name])
                    else:
                        raise ArgumentError(f"Duplicate argument: {param_name}")
                        
                named_args[param_name] = item.value
            else:
                positional_args.append(item)
        
        # Process parameters in order
        positional_index = 0
        
        for param in self.parameters:
            param_name = param.name
            
            # 1. Handle VAR_POSITIONAL (*args)
            if param.kind == ParameterKind.VAR_POSITIONAL:
                for arg in positional_args[positional_index:]:
                    converted = await self._convert_argument(ctx, arg, param)
                    args.append(converted)
                positional_index = len(positional_args)
                continue
            
            # 2. Handle VAR_KEYWORD (**kwargs)
            if param.kind == ParameterKind.VAR_KEYWORD:
                for name, value in list(named_args.items()):
                    converted = await self._convert_argument(ctx, value, param)
                    kwargs[name] = converted
                    del named_args[name]
                continue
            
            # 3. Handle specific argument (Positional, Keyword, or both)
            
            # Check if provided by name
            if param_name in named_args:
                val = named_args[param_name]
                del named_args[param_name]
                
                converted = await self._convert_argument(ctx, val, param)
                kwargs[param_name] = converted
                continue
            
            # If KEYWORD_ONLY and not in named_args (checked above)
            if param.kind == ParameterKind.KEYWORD_ONLY:
                if param.default != inspect.Parameter.empty:
                    converted = await self._convert_argument(ctx, param.default, param)
                    kwargs[param_name] = converted
                elif param.is_optional:
                    kwargs[param_name] = None
                else:
                    raise MissingRequiredArgumentError(param)
                continue
                
            # Try to get from positional args
            if positional_index < len(positional_args):
                val = positional_args[positional_index]
                positional_index += 1
                
                # Handle greedy/regex consumption
                is_last_param = param is self.parameters[-1]
                
                if ((is_last_param and param.annotation == str) or param.greedy) and positional_index < len(positional_args):
                    # Consume remaining positional args as a single string
                    remaining = positional_args[positional_index:]
                    val = val + ' ' + ' '.join(remaining)
                    positional_index = len(positional_args)
                elif param.regex:
                    # Iteratively consume tokens as long as they match the regex
                    current_val = val
                    tokens_consumed = 0
                    
                    # Only attempt to extend if the base value matches
                    if re.match(param.regex, current_val):
                        remaining_tokens = positional_args[positional_index:]
                        for token in remaining_tokens:
                            next_val = current_val + " " + token
                            if re.match(param.regex, next_val):
                                current_val = next_val
                                tokens_consumed += 1
                            else:
                                break
                    
                    val = current_val
                    positional_index += tokens_consumed
                
                converted = await self._convert_argument(ctx, val, param)
                
                # Decide where to put it
                if param.kind == ParameterKind.POSITIONAL_ONLY:
                    args.append(converted)
                else:
                    kwargs[param_name] = converted
            else:
                # Not provided positionally
                if param.default != inspect.Parameter.empty:
                    converted = await self._convert_argument(ctx, param.default, param)
                    kwargs[param_name] = converted
                elif param.is_optional:
                    kwargs[param_name] = None
                else:
                    raise MissingRequiredArgumentError(param)

        if len(named_args) > 0:
            raise UnknownArgumentError(list(named_args.keys()))
        if positional_index < len(positional_args):
            raise UnknownArgumentError(positional_args[positional_index:])

        return args, kwargs

    async def invoke(self, ctx: Context, *args, **kwargs):
        try:
            await self._run_checks(ctx)
            await self._check_cooldown(ctx)
            
            # Parse arguments if parameters are defined
            if self.parameters:
                parsed_args, parsed_kwargs = await self._parse_arguments(ctx)
                # Combine parsed args with provided args
                # Usually dispatch provides args/kwargs, but for commands/redeems they come from parsing
                # If dispatch provided args, we might want to prepend them?
                # For now, let's assume if parameters are defined, we use parsed args.
                args = (*args, *parsed_args)
                kwargs = {**kwargs, **parsed_kwargs}

            await self._run_verification(ctx, *args, **kwargs)
            result = self.func(ctx, *args, **kwargs)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            # We need a way to bubble up errors or handle them
            raise e


class Dispatcher:
    def __init__(self, bot: 'Commands'):
        self.bot = bot
        self.listeners: Dict[str, EventListener] = {}

    def register(self, listener: EventListener):
        if listener.name in self.listeners:
            raise CommandRegistrationError(listener.name, "Listener")
        self.listeners[listener.name] = listener
        for alias in listener.aliases:
            if alias in self.listeners:
                raise CommandRegistrationError(alias, "Listener alias")
            self.listeners[alias] = listener

    async def dispatch(self, ctx: Context, key: str, *args, **kwargs):
        if key in self.listeners:
            try:
                await self.listeners[key].invoke(ctx, *args, **kwargs)
            except Exception as e:
                await self.handle_error(ctx, self.listeners[key], e)

    async def handle_error(self, ctx: Context, listener: EventListener, error: Exception):
        # Default error handling
        logger.error(f"Error in listener '{listener.name}':", exc_info=True)


class CommandDispatcher(Dispatcher):
    async def handle_error(self, ctx: Context, listener: EventListener, error: Exception):
        if hasattr(self.bot, 'on_command_error'):
            await self.bot.on_command_error(ctx, listener, error)
        else:
            await super().handle_error(ctx, listener, error)

class RedeemDispatcher(Dispatcher):
    async def handle_error(self, ctx: Context, listener: EventListener, error: Exception):
        if hasattr(self.bot, 'on_redeem_error'):
            await self.bot.on_redeem_error(ctx, listener, error)
        else:
            await super().handle_error(ctx, listener, error)


class Check:
    """To display a custom error message when conversion fails,
    raise command_exceptions.CheckError in the convert method."""
    title: str = None
    short_help: str = None
    help: str = None
    hide_in_help: bool = False

    async def check(self, ctx: Context) -> bool:
        raise NotImplementedError


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

        self.error_cooldown = Cooldown(1, 5, [BucketType.USER, BucketType.CHANNEL])
        
        self.dispatchers: Dict[str, Dispatcher] = {
            "command": CommandDispatcher(self),
            "twitch_redeem": RedeemDispatcher(self)
        }
        
        self.prefix: str = "!"
        self.loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self.cog_manager: Optional[CogManager] = None

    def add_dispatcher(self, event_type: str, dispatcher: Dispatcher):
        self.dispatchers[event_type] = dispatcher

    def add_listener(self, listener: EventListener):
        if listener.event_type not in self.dispatchers:
            # Create a default dispatcher if one doesn't exist
            self.dispatchers[listener.event_type] = Dispatcher(self)
        
        self.dispatchers[listener.event_type].register(listener)
                    
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
        usage_text = command.get_usage_text(ctx.prefix, ctx.invoked_with)
        if isinstance(error, CommandOnCooldown):
            if error.cooldown.per >= 60 and self.error_cooldown.get_retry_after(ctx) <= 0:
                await ctx.send(f"❌ Command '{command.name}' is on cooldown. Try again in {format_seconds(error.retry_after, TimeSize.LONG)}.")
                self.error_cooldown.update_rate_limit(ctx)
        elif isinstance(error, MissingRequiredArgumentError):
            await ctx.send(f"❌ Usage: {usage_text} – Missing argument: {error.parameter.name}")
        elif isinstance(error, EmptyFlagValueError):
            await ctx.send(f"❌ Expected a value for '{error.parameter.name}' (type: {error.parameter.type_title})")
        elif isinstance(error, ArgumentConversionError):
            out_text = f"❌ Error turning '{error.value}' ({error.parameter.name}) into type {error.parameter.type_title}"
            if error.message:
                out_text += f": {error.message}"
            await ctx.send(out_text)
        elif isinstance(error, UnknownArgumentError):
            await ctx.send(f"❌ Usage: {usage_text} – Unknown argument: {error.arguments[0]}")
        elif isinstance(error, UnknownFlagError):
            await ctx.send(f"❌ Usage: {usage_text} – Unknown parameter: {error.flag_name}")
        elif isinstance(error, CheckFailure):
            if self.error_cooldown.get_retry_after(ctx) <= 0:
                await ctx.send(f"❌ {error.message}")
                self.error_cooldown.update_rate_limit(ctx)
        elif isinstance(error, VerificationFailure):
            await ctx.send(f"❌ {error.message}")
        elif isinstance(error, ArgumentError):
            await ctx.send(f"❌ {error.message}")
        elif isinstance(error, CommandError):
            await ctx.send(f"❌ {error.message}")
        else:
            await ctx.send(f"❌ An error occurred")
        
    
    async def on_redeem_error(self, ctx: TwitchRedeemContext, redeem: TwitchRedeem, error: Exception):
        await ctx.send(f"❌ An error occurred. Points will be refunded.")
        try:
            await ctx.update_status(CustomRewardRedemptionStatus.CANCELED)
        except Exception:
            await ctx.send(f"❌ Couldn't refund points.")
        
    def _find_command(self, text: str) -> tuple[str, str]:
        # text_lower = text.lower()
        for cmd in sorted(self.dispatchers["command"].listeners.keys(), key=len, reverse=True):
            if text == cmd or text.startswith(cmd + ' '):
                return cmd, text[len(cmd):].strip()
        return None, text

    def _find_redeem(self, name: str) -> tuple[str, str]:
        # name_lower = name.lower()
        for redeem in sorted(self.dispatchers["twitch_redeem"].listeners.keys(), key=len, reverse=True):
            if name == redeem:
                return redeem, name
        return None, name

    async def process_twitch_message(self, msg: ChatMessage) -> None:
        await self.process_message(Platform.TWITCH, msg)

    async def process_message(self, platform: Platform, platform_context: Any):
        ctx: Context = None
        if platform == Platform.TWITCH and isinstance(platform_context, ChatMessage):
            ctx = TwitchContext(platform_context, self.twitches.get(platform_context.room.room_id))
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
        if not command_name or command_name not in self.dispatchers["command"].listeners:
            return

        command = self.dispatchers["command"].listeners[command_name]

        ctx.command = command
        ctx.prefix = used_prefix
        ctx.invoked_with = content[:len(command_name)]
        ctx.parameters = remaining_text

        await self.dispatchers["command"].dispatch(ctx, command_name)

    async def process_channel_point_redemption(self, redemption: ChannelPointsCustomRewardRedemptionData):
        redeem_name, remaining_text = self._find_redeem(redemption.reward.title)
        if not redeem_name:
            return
            
        ctx = TwitchRedeemContext(redemption, self)
        await self.dispatchers["twitch_redeem"].dispatch(ctx, redeem_name)


class Command(EventListener):       
    def __init__(self, name: str, func: CommandFunc, bot: 'Commands', checks: List[Check] = None, aliases: List[str] = [], hidden: bool = False, help: str = None, short_help: str = None, title: str = None, verifier: Callable = None, cog: 'Cog' = None):
        super().__init__(name, func, "command", checks, None, verifier, aliases)
        self.bot = bot
        self.cog = cog
        self.hidden = hidden
        
        # Load cooldown from decorator if present
        if not self.cooldown:
            self.cooldown = getattr(func, '_command_cooldown', None)
        
        # Parse docstring for parameter descriptions and help text
        doc = parse(func.__doc__ or "")
        
        self.title = title or name.replace('_', ' ').title()
        self.short_help = short_help or doc.short_description
        self.help = help or doc.long_description or doc.short_description
        
    async def verify(self, ctx: Context):
        await self._run_checks(ctx)
        await self._check_cooldown(ctx)
        args, kwargs = await self._parse_arguments(ctx)
        await self._run_verification(ctx, args, kwargs)
        return True

    async def invoke(self, ctx: Context) -> None:
        """Invoke the command with the given context."""
        try:
            await super().invoke(ctx)
        except Exception as e:
            ignore_list = (
                CommandOnCooldown, UnknownFlagError,
                DuplicateParameterError, MissingRequiredArgumentError,
                UnknownArgumentError, ArgumentConversionError,
            )
            if not isinstance(e, ignore_list):
                logger.error("Error in command invocation:", exc_info=True)
            if hasattr(self.bot, 'on_command_error'):
                await self.bot.on_command_error(ctx, self, e)
    
    def get_usage_text(self, prefix: str, invoked_name: str = None):
        if not invoked_name:
            invoked_name = self.name
        nm_out = [f"{prefix}{invoked_name}"]
        
        for param in self.parameters:
            if param.hidden:
                continue
            param_str = param.get_parameter_display()
            nm_out.append(param_str)
        
        return " ".join(nm_out)
        
    def get_help_text(self, prefix: str, invoked_name: str = None):
        if not invoked_name:
            invoked_name = self.name
        description = self.short_help or self.help or ""
        aliases = ""
        name_and_usage = self.get_usage_text(prefix, invoked_name)
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
                restr_to.append(check.title or check.short_help or check.help or check.__qualname__)
            restrictions = f"Limited to: {', '.join(restr_to).capitalize()}"
        cooldowns = ""
        if self.cooldown:
            cd_buckets = ', '.join([b.name.lower() for b in self.cooldown.bucket])
            if self.cooldown.rate == 1:
                cooldowns = f"Cooldown: {self.cooldown.per}s ({cd_buckets})"
            else:
                cooldowns = f"Cooldown: {self.cooldown.rate}x/{self.cooldown.per}s ({cd_buckets})"

        response = strjoin(' – ', name_and_usage, description, restrictions, aliases, cooldowns)
        return response


class TwitchRedeem(EventListener):
    def __init__(self, name: str, func: TwitchRedeemFunc, bot: Commands, checks: List[Check] = None, cooldown: Cooldown = None, **kwargs):
        super().__init__(name, func, "twitch_redeem", checks, cooldown)
        self.bot = bot
        
    async def invoke(self, ctx: TwitchRedeemContext) -> None:
        try:
            await super().invoke(ctx)
        except Exception as e:
            if hasattr(ctx, 'bot') and hasattr(self.bot, 'on_redeem_error'):
                await self.bot.on_redeem_error(ctx, self, e)
            else:
                logger.error("Error in redeem invocation:", exc_info=True)


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

def cooldown(rate: int, per: float, type: Union[BucketType, List[BucketType]] = BucketType.DEFAULT):
    """Decorator to apply a cooldown to a command.
    
    Args:
        rate: Number of uses allowed.
        per: Time period in seconds.
        type: The bucket type for the cooldown.
    """
    def decorator(func):
        func._command_cooldown = Cooldown(rate, per, type)
        return func
    return decorator

def verification(verifier_func):
    """Decorator to add a verification function to a command.
    
    The verifier function should accept (ctx, *args, **kwargs) matching the command's signature.
    It should return True (pass), False (fail), or a string (fail with message).
    """
    def decorator(func):
        func._command_verifier = verifier_func
        return func
    return decorator

def parameter(
    name: str, aliases: Union[str, List[str]] = [],
    greedy: bool = False, hidden: bool = False,
    help: str = None, regex: str = None,
    display_name: str = None, converter: Converter = None
    ):
    """Decorator to configure a command parameter.
    
    Args:
        name: The name of the parameter to configure.
        aliases: Optional alias or list of aliases for the parameter.
        greedy: If True, the parameter will consume all remaining input as a single string.
        hidden: If True, the parameter will be hidden from help documentation.
        help: Help text for the parameter.
        regex: Regex pattern to match for this parameter.
    """
    def decorator(func):
        if not hasattr(func, '_command_params'):
            func._command_params = {}
        func._command_params[name] = {
            'aliases': aliases,
            'greedy': greedy,
            'hidden': hidden,
            'help': help,
            'regex': regex,
            'display_name': display_name,
            'converter': converter
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
                    current.append('"')
                    in_quotes = char
                elif char == in_quotes:
                    # End of quoted string
                    current.append('"')
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
                if isinstance(flag_value, str) and flag_value[0] == '"' and flag_value[-1] == '"':
                    flag_value = flag_value[1:-1]
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
