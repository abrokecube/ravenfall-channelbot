from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Awaitable, get_origin, get_args, Union, Any
from uuid import uuid4
import inspect

if TYPE_CHECKING:
    from .events import BaseEvent, MessageEvent, CommandEvent
    from .cooldown import Cooldown
    from .checks import BaseCheck
    from .cog import Cog
from .global_context import GlobalContext
from .converters import BaseConverter
from .modals import MetaFilter, Parameter, ParameterKind, BUILTIN_TYPE_DOCS, Flag
from .enums import Dispatcher
from .exceptions import (
    ListenerOnCooldown,
    VerificationFailure,
    EmptyFlagValueError,
    ArgumentConversionError,
    UnknownFlagError,
    DuplicateParameterError, 
    ArgumentError,
    MissingRequiredArgumentError,
    UnknownArgumentError,
    CheckFailure
)

import asyncio
import docstring_parser
import logging
import re
from utils.utils import strjoin

LOGGER = logging.getLogger(__name__)

class BaseListener:
    def __init__(self, func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]], cog: Cog = None):
        self._id: str = f"{func.__name__}_{uuid4()}"
        self.expected_dispatcher: Dispatcher = Dispatcher.Base
        self.func = func
        self.cog: Cog = None
    
    async def check_for_match(self, event: BaseEvent) -> bool:
        return True
    
    async def _run_func(self, global_ctx: GlobalContext, event: BaseEvent, *args, **kwargs):
        try:
            if self.cog is not None:
                result = self.func(event, *args, **kwargs)
                if asyncio.iscoroutine(result):
                    await result
            else:
                result = self.func(global_ctx, event, *args, **kwargs)
                if asyncio.iscoroutine(result):
                    await result
        except Exception as e:
            await self.on_func_exception(global_ctx, event, e, *args, **kwargs)
            raise e
                
    async def invoke(self, global_ctx: GlobalContext, event: BaseEvent, *args, **kwargs) -> None:
        await self._run_func(global_ctx, event, *args, **kwargs)
        
    async def on_func_exception(self, global_ctx: GlobalContext, event: BaseEvent, error: Exception, *args, **kwargs) -> None:
        pass

class GenericListener(BaseListener):
    def __init__(
        self, func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]], cog: Cog = None,
        cooldown: Cooldown | None = None, 
        expected_dispatcher: Dispatcher = Dispatcher.Generic
        ):
        super().__init__(func, cog)
        self.expected_dispatcher: Dispatcher = getattr(func, '_listener_dispatcher', expected_dispatcher)
        self.meta_filter: MetaFilter = getattr(func, '_listener_meta_filter', MetaFilter([], False, [], False))
        self.cooldown: Cooldown | None = getattr(func, '_listener_cooldown', cooldown)

    async def check_for_match(self, event: BaseEvent):
        matches_categories = event.categories in self.meta_filter.categories
        if not self.meta_filter.invert_categories:
            matches_categories = not matches_categories
        matches_platforms = event.platform in self.meta_filter.platforms
        if not self.meta_filter.invert_platforms:
            matches_platforms = not matches_platforms
        return matches_categories and matches_platforms

    async def _check_cooldown(self, event: BaseEvent):
        if self.cooldown:
            retry_after = self.cooldown.get_retry_after(event)
            if retry_after > 0:
                raise ListenerOnCooldown(self.cooldown, retry_after)
            self.cooldown.update_rate_limit(event)
        return True
    
    async def invoke(self, global_ctx, event, match_result: Any):
        await self._check_cooldown(event)
        await self._run_func(global_ctx, event, match_result)

class LambdaListener(GenericListener):
    def __init__(self, func, cog = None, cooldown = None,
        event_types: list[type[BaseEvent]] = [],
        match_fn: Callable[[BaseEvent], bool] = lambda x: True,
        expected_dispatcher: Dispatcher = Dispatcher.Generic
        ):
        super().__init__(func, cog, cooldown, expected_dispatcher)
        self.event_types: tuple[type[BaseEvent]] = tuple(event_types)
        self.match_fn: Callable[[BaseEvent], bool] = match_fn
        
    async def check_for_match(self, event):
        if not isinstance(event, self.event_types):
            return False
        return self.match_fn(event)

class CommandListener(GenericListener):
    def __init__(
        self, func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]], cog: 'Cog' = None,
        name: str = None, aliases: list[str] = [], cooldown = None, checks: list[BaseCheck] = None,
        verifier: Callable = None, hidden: bool = False, 
        help: str = None, short_help: str = None, title: str = None,
        expected_dispatcher: Dispatcher = Dispatcher.Command
        ):
        super().__init__(func, cog, cooldown, expected_dispatcher)
        self.verifier: Callable = getattr(func, '_listener_command_verifier', verifier)
        
        self.checks: list[BaseCheck] = []
        if checks:
            self.checks.extend(checks)
        self.checks.extend(getattr(func, '_listener_command_checks', []))
        
        self.name = name or func.__name__
        self.aliases = []
        if aliases:
            self.aliases.extend(aliases)
        
        self._id = self.name
        
        doc = docstring_parser.parse(func.__doc__ or "")
        
        self.title = title or self.name.replace('_', ' ').title()
        self.short_help = short_help or doc.short_description
        self.help = help or doc.long_description or doc.short_description

        self.hidden = hidden
        
        # Store signature and resolve type hints
        self.signature = inspect.signature(func)
        try:
            # get_type_hints resolves string annotations to actual types
            from typing import get_type_hints
            self.type_hints = get_type_hints(func)
        except Exception as e:
            # If get_type_hints fails, we'll fall back to the signature
            LOGGER.warning(f"Could not resolve type hints for {func.__name__}: {e}")
            self.type_hints = {}

        # Load parameter configurations from decorator
        params_config: dict = getattr(func, '_listener_command_params', {})
        
        kind_mapping = {
            inspect.Parameter.POSITIONAL_ONLY: ParameterKind.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD: ParameterKind.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL: ParameterKind.VAR_POSITIONAL,
            inspect.Parameter.KEYWORD_ONLY: ParameterKind.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD: ParameterKind.VAR_KEYWORD
        }

        self.parameters: list[Parameter] = []
        self.parameters_map: dict[str, Parameter] = {}
        self.arg_mappings: dict[str, str] = {}
        
        doc_params = {p.arg_name: p.description for p in doc.params}

        # Process parameters
        # Skip 'self' (if bound) and 'ctx'
        sig_params = list(self.signature.parameters.values())
        if sig_params and sig_params[0].name == 'self':
            sig_params.pop(0)
        if sig_params and (sig_params[0].name == 'g_ctx' or sig_params[0].annotation == GlobalContext):
            sig_params.pop(0)
        if sig_params and (sig_params[0].name in ['event', 'ctx'] or sig_params[0].annotation == CommandEvent or 'Event' in str(sig_params[0].annotation)):
            sig_params.pop(0)
            
        for param in sig_params:
            param_config: dict = params_config.get(param.name, {})
            
            # Resolve aliases
            aliases = param_config.get('aliases', [])
            if isinstance(aliases, str):
                aliases = [aliases]
            
            display_name = param_config.get('display_name', None) or param.name

            # Resolve type and check for Optional
            raw_annotation = self.type_hints.get(param.name, param.annotation)
            annotation = raw_annotation
            is_optional = False
            
            # If still a string (shouldn't happen with get_type_hints, but just in case)
            if isinstance(annotation, str):
                # Try to evaluate common built-in types
                builtins_map = {
                    'int': int,
                    'float': float,
                    'str': str,
                    'bool': bool,
                }
                if annotation in builtins_map:
                    annotation = builtins_map[annotation]

            # Handle Optional[T] - extract the inner type
            origin = get_origin(annotation)
            if origin is Union:
                args = get_args(annotation)
                # Check if NoneType is in args
                if type(None) in args:
                    is_optional = True
                    # Filter out NoneType to get the actual type
                    non_none_types = [t for t in args if t is not type(None)]
                    if non_none_types:
                        annotation = non_none_types[0]
            
            is_optional = is_optional or \
                (param.default != inspect.Parameter.empty) or \
                (param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD))

            # Get help text
            param_help = param_config.get('help')
            if not param_help:
                param_help = doc_params.get(param.name)
                
            converter = param_config.get('converter', None) or annotation
            if not converter:
                converter = str

            # Create Parameter object
            p = Parameter(
                name=param.name,
                display_name=display_name,
                raw_annotation=raw_annotation,
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
            is_subclass = isinstance(converter, type) and issubclass(converter, BaseConverter)
            is_instance = isinstance(converter, BaseConverter)
            if is_subclass or is_instance:
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

    async def check_for_match(self, event: CommandEvent) -> bool:
        return False

    async def _run_checks(self, g_ctx: GlobalContext, ctx: CommandEvent):
        for check in self.checks:
            try:
                check_result = check.check(g_ctx, ctx)
                if asyncio.iscoroutine(check_result):
                    check_result = await check_result
                
                if isinstance(check_result, str):
                    raise CheckFailure(check_result)
                if not check_result:
                    raise CheckFailure(f"Check failed for command '{self.name}'")
            except CheckFailure:
                raise
            except Exception as e:
                raise e
                # raise (f"Check raised an error: {e}")
        return True

    async def _run_verification(self, event: CommandEvent, *args, **kwargs):
        # Run verifier if present
        if self.verifier:
            try:
                verify_result = self.verifier(event, *args, **kwargs)
                if asyncio.iscoroutine(verify_result):
                    verify_result = await verify_result
                
                if isinstance(verify_result, str):
                    raise VerificationFailure(verify_result)
                if verify_result is False:
                    raise VerificationFailure(f"Verification failed for command '{self.name}'")
            except VerificationFailure:
                raise
            except Exception as e:
                raise VerificationFailure("An unknown error occurred")
        return True

    async def _convert_argument(self, ctx: CommandEvent, value: str | Any, param: Parameter, g_ctx: GlobalContext = None) -> Any:
        if value is None:
            return value
        
        if param.annotation == inspect.Parameter.empty:
            return value
            
        conv_obj = param.converter
        
        # Check for custom converter
        if hasattr(conv_obj, 'convert'):
            if value == True:
                raise EmptyFlagValueError(param)
            if not isinstance(value, str):
                if type(value) is type(conv_obj):
                    return value
            try:
                # Inspect the convert method's signature to determine parameters
                convert_method = getattr(conv_obj, 'convert')
                # sig = inspect.signature(convert_method)
                # params = list(sig.parameters.values())
                
                # if params and params[0].name in ('self', 'cls'):
                #     params.pop(0)
                
                # if len(params) >= 2:
                #     first_param = params[0]
                    
                #     if first_param.annotation == 'GlobalContext':
                #         result = convert_method(g_ctx, ctx, value)
                #     else:
                #         result = convert_method(ctx, value)
                # else:
                #     result = convert_method(ctx, value)
                
                result = convert_method(g_ctx, ctx, value)
                if asyncio.iscoroutine(result):
                    return await result
                return result
            except ArgumentConversionError as e:
                raise ArgumentConversionError(e.message, value, param)
            except Exception as e:
                raise ArgumentConversionError(None, value, param, e)
                        
        if conv_obj is bool:
            if isinstance(value, bool):
                return value
            return value.lower() in ('true', 'yes', '1', 'on')
        elif conv_obj is int:
            try:
                return int(value)
            except ValueError as e:
                raise ArgumentConversionError("Expected an integer", value, param, e)
        elif conv_obj is float:
            try:
                return float(value)
            except ValueError as e:
                raise ArgumentConversionError("Expected a number", value, param, e)
        elif conv_obj is str:
            if value == True:
                raise EmptyFlagValueError(param)
            return value
        else:
            # Attempt to call the type as a constructor
            try:
                return conv_obj(value)
            except Exception as e:
                raise ArgumentConversionError(f"Could not convert to {conv_obj.__name__}", value, param, e)    

    async def _parse_arguments(self, ctx: CommandEvent, g_ctx: GlobalContext) -> tuple[list, dict]:
        args = []
        kwargs = {}
        
        # Separate positional args and flags from ctx.args
        positional_args = []
        named_args = {}
        parsed_args = ctx.parsed_args

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
                    converted = await self._convert_argument(ctx, arg, param, g_ctx)
                    args.append(converted)
                positional_index = len(positional_args)
                continue
            
            # 2. Handle VAR_KEYWORD (**kwargs)
            if param.kind == ParameterKind.VAR_KEYWORD:
                for name, value in list(named_args.items()):
                    converted = await self._convert_argument(ctx, value, param, g_ctx)
                    kwargs[name] = converted
                    del named_args[name]
                continue
            
            # 3. Handle specific argument (Positional, Keyword, or both)
            
            # Check if provided by name
            if param_name in named_args:
                val = named_args[param_name]
                del named_args[param_name]
                
                converted = await self._convert_argument(ctx, val, param, g_ctx)
                kwargs[param_name] = converted
                continue
            
            # If KEYWORD_ONLY and not in named_args (checked above)
            if param.kind == ParameterKind.KEYWORD_ONLY:
                if param.default != inspect.Parameter.empty:
                    converted = await self._convert_argument(ctx, param.default, param, g_ctx)
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
                
                converted = await self._convert_argument(ctx, val, param, g_ctx)
                
                # Decide where to put it
                if param.kind == ParameterKind.POSITIONAL_ONLY:
                    args.append(converted)
                else:
                    kwargs[param_name] = converted
            else:
                # Not provided positionally
                if param.default != inspect.Parameter.empty:
                    converted = await self._convert_argument(ctx, param.default, param, g_ctx)
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

    async def invoke(self, global_ctx, event, *args, **kwargs):
        await self._run_checks(global_ctx, event)
        await self._check_cooldown(event)
        if self.parameters:
            parsed_args, parsed_kwargs = await self._parse_arguments(event, global_ctx)
            args = (*args, *parsed_args)
            kwargs = {**kwargs, **parsed_kwargs}
        await self._run_verification(event, *args, **kwargs)
        await self._run_func(global_ctx, event, *args, **kwargs)

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

