from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Awaitable, get_origin, get_args, Union, Any
from uuid import uuid4
import inspect

if TYPE_CHECKING:
    from .events import BaseEvent, MessageEvent, CommandEvent
    from .global_context import GlobalContext
    from .cooldown import Cooldown
    from .checks import Check
    from .converters import BaseConverter
    from .cog import Cog
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
    
    async def invoke(self, global_ctx: GlobalContext, event: BaseEvent) -> None:
        from_cog = getattr(self.func, "_listener_from_cog", False)
        if from_cog:
            if inspect.isawaitable(self.func):
                await self.func(event)
            else:
                self.func(event)
        else:
            if inspect.isawaitable(self.func):
                await self.func(global_ctx, event)
            else:
                self.func(global_ctx, event)

class GenericListener(BaseListener):
    def __init__(
        self, func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]], cog: Cog = None,
        cooldown: Cooldown | None = None
        ):
        super().__init__(func, cog)
        self.expected_dispatcher: Dispatcher = Dispatcher.Generic
        self.meta_filter: MetaFilter = getattr(func, '_listener_meta_filter', MetaFilter([], False, [], False))
        self.cooldown: Cooldown | None = getattr(func, '_listener_cooldown', cooldown)

    async def check_for_match(self, event: BaseEvent):
        matches_categories = event.category in self.meta_filter.categories
        if not self.meta_filter.categories_exclusive:
            matches_categories = not matches_categories
        matches_platforms = event.platform in self.meta_filter.platforms
        if not self.meta_filter.platforms_exclusive:
            matches_platforms = not matches_platforms
        return matches_categories and matches_platforms

    async def _check_cooldown(self, event: BaseEvent):
        if self.cooldown:
            retry_after = self.cooldown.get_retry_after(event)
            if retry_after > 0:
                raise ListenerOnCooldown(self.cooldown, retry_after)
            self.cooldown.update_rate_limit(event)
        return True
    
    async def invoke(self, global_ctx, event):
        await self._check_cooldown(event)
        return await super().invoke(global_ctx, event)

class CommandListener(GenericListener):
    def __init__(
        self, func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]], cog: 'Cog' = None,
        cooldown = None, name: str = None, checks: list[Check] = None, verifier: Callable = None, 
        aliases: list[str] = [], hidden: bool = False, 
        help: str = None, short_help: str = None, title: str = None
        ):
        super().__init__(func, cog, cooldown)
        self.verifier: Callable = getattr(func, '_listener_command_verifier', verifier)
        checks.extend(getattr(func, '_listener_command_checks', []))
        self.checks = checks
        self.name = name or func.__name__
        self.aliases = []
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
        event.invoked_with == self.name

    async def _run_checks(self, ctx: CommandEvent):
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
                raise VerificationFailure("There was an error during verification")
        return True

    async def _convert_argument(self, ctx: CommandEvent, value: str | Any, param: Parameter) -> Any:
        if value is None:
            return value
        
        if param.annotation == inspect.Parameter.empty:
            return value
            
        type_ = param.converter
        
        # Check for custom converter
        if hasattr(type_, 'convert') and inspect.iscoroutinefunction(type_.convert):
            if value == True:
                raise EmptyFlagValueError(param)
            if not isinstance(value, str):
                if type(value) is type(type_):
                    return value
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

    async def _parse_arguments(self, ctx: CommandEvent) -> tuple[list, dict]:
        args = []
        kwargs = {}
        
        # Separate positional args and flags from ctx.args
        positional_args = []
        named_args = {}
        parsed_args = CommandArgs(ctx.parameters_text)

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

    async def invoke(self, global_ctx, event, *args, **kwargs):
        await self._run_checks(event)
        await self._check_cooldown(event)
        if self.parameters:
            parsed_args, parsed_kwargs = await self._parse_arguments(event)
            args = (*args, *parsed_args)
            kwargs = {**kwargs, **parsed_kwargs}
        await self._run_verification(event, *args, **kwargs)
        
        from_cog = getattr(self.func, "_listener_from_cog", False)
        if from_cog:
            result = self.func(event, *args, **kwargs)
            if asyncio.iscoroutine(result):
                await result
        else:
            result = self.func(global_ctx, event, *args, **kwargs)
            if asyncio.iscoroutine(result):
                await result

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

DELIMETERS = ('=', ':')
RE_FLAG = re.compile(r'[-a-zA-Z]{2}[a-zA-Z]+[:=]+.+|-[a-zA-Z]\b|--[a-zA-Z_]+\b')

class CommandArgs:
    def __init__(self, text: str):
        self.text = text
        
        self.args: list[str | Flag] = []  # args are in order of appearance
        self.flags: list[Flag] = []  # flags are in order of appearance
        self.grouped_args: list[str] = []  # consecutive non-flag args joined by space
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
