from __future__ import annotations
from typing import Any, override, NamedTuple
from bot.core.components import GlobalContext, BaseEvent
from bot.integrations.chat_messages import MessageEvent
from bot.core.enums import EventCategory, EventSource
from .enums import ParameterType
from dataclasses import dataclass, field
from collections.abc import Collection
import re


class BaseConverter:
    """To display a custom error message when conversion fails,
    raise command_exceptions.ArgumentConversionError in the convert method."""
    title: str = ""
    short_help: str = ""
    help: str = ""

    async def convert(self, g_ctx: GlobalContext, event: CommandEvent, arg: str) -> Any:  # pyright: ignore[reportUnusedParameter, reportAny, reportExplicitAny]
        raise NotImplementedError

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
        current: list[str] = []
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
                flag_value: str | None = None
                if has_delimiter:
                    if delimiter_char and delimiter_char in flag_name:
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

    # def get_flag(self, name: str | list[str], case_sensitive: bool = False, default: str | None = None) -> Flag | None:
    #     names = name if isinstance(name, list) else [name]
    #     for flag in self.flags:
    #         if case_sensitive and flag.name in names:
    #             return flag
    #         elif not case_sensitive and flag.name.lower() in [n.lower() for n in names]:
    #             return flag
    #     return Flag(name, default)

@dataclass
class Parameter:
    name: str
    display_name: str
    raw_annotation: Any
    annotation: Any
    converter: BaseConverter | type
    kind: ParameterType
    default: Any = inspect.Parameter.empty
    aliases: list[str] = field(default_factory=list)
    greedy: bool = False
    hidden: bool = False
    is_optional: bool = False
    type_title: str | None = None
    type_short_help: str | None = None
    type_help: str | None = None
    help: str | None = None
    command: CommandListener | None = None
    regex: str | re.Pattern[str] | None = None
    _regex_compiled: re.Pattern[str] | None = None

    def __post_init__(self):
        if isinstance(self.regex, str):
            self._regex_compiled = re.compile(self.regex)
        elif isinstance(self.regex, re.Pattern):
            self._regex_compiled = self.regex
    
    def get_parameter_display(self, invoked_name: str | None = None) -> str:
        param_str = invoked_name or self.display_name
        if self.type_title:
            param_str += f": {self.type_title}"
        if self.kind == ParameterType.KEYWORD_ONLY:
            if len(param_str) == 1:
                param_str = f"(-{param_str})"
            else:
                param_str = f"(--{param_str})"
        elif self.is_optional:
            param_str = f"({param_str})"
        else:
            param_str = f"<{param_str}>"
        return param_str
    
    def get_help_text(self, invoked_name: str | None = None) -> str:
        param_aliases = self.aliases[:]
        
        if invoked_name in param_aliases:
            param_aliases.remove(invoked_name)
            param_aliases.append(self.name)
        if self.display_name != self.name and invoked_name == self.name:
            param_aliases.append(self.display_name)
        param_aliases.sort()

        out_str: list[str] = []
        param_str = self.get_parameter_display(invoked_name)
        out_str.append(param_str)
        help_text = self.help
        type_help = self.type_short_help or self.type_help or None            
        if not help_text:
            if self.kind == ParameterType.VAR_KEYWORD:
                help_text = "Command accepts any named argument"
            elif self.kind == ParameterType.VAR_POSITIONAL:
                help_text = "Command accepts any additional arguments"
            elif type_help:
                help_text = type_help
                type_help = None
        if help_text:
            out_str.append(help_text)
        properties: list[str] = []
        if self.is_optional:
            properties.append("optional")
        else:
            properties.append("required")
        if self.kind == ParameterType.KEYWORD_ONLY:
            properties.append("keyword-only")
        out_str.append(f"{', '.join(properties)}".capitalize())
        if self.default != inspect.Parameter.empty and self.default != False:
            out_str.append(f"Default: {self.default}")
        if type_help:
            out_str.append(f"Expects {self.type_title}: {type_help}")
        if param_aliases:
            out_str.append(f"Aliases: {', '.join(param_aliases)}")
            
        response = strjoin(' – ', *out_str)
        return response

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



@dataclass(kw_only=True)
class CommandEvent(BaseEvent):
    categories: Collection[EventCategory] = (EventCategory.Command,)
    platform: EventSource = EventSource.Any
    data: Any | None = None
    message: MessageEvent
    prefix: str
    invoked_with: str
    parsed_args: CommandArgs
    parameters_text: str
    specified_parameters: set[str] = field(default_factory=set)


class CommandListener(GenericListener):
    def __init__(
        self, 
        func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]], 
        cog: 'Cog | None' = None,
        name: str | None = None, 
        aliases: list[str] | None = None, 
        cooldown: Cooldown | None = None, 
        checks: list[BaseCheck] | None = None,
        verifier: VerifierType | None = None, 
        hidden: bool = False, 
        help: str | None = None, short_help: str | None = None, title: str | None = None,
        expected_dispatcher: Dispatcher = Dispatcher.Command
        ):
        super().__init__(func, cog, cooldown, expected_dispatcher)
        self.verifier: VerifierType | None = getattr(func, '_listener_command_verifier', verifier)
        
        self.checks: list[BaseCheck] = []
        if checks:
            self.checks.extend(checks)
        self.checks.extend(getattr(func, '_listener_command_checks', []))
        
        self.name: str = name or func.__name__
        self.aliases: list[str] = []
        if aliases:
            self.aliases.extend(aliases)
        
        self._id: str = self.name
        
        doc = docstring_parser.parse(func.__doc__ or "")
        
        self.title: str = title or self.name.replace('_', ' ').title()
        self.short_help: str | None = short_help or doc.short_description
        self.help: str | None = help or doc.long_description or doc.short_description

        self.hidden: bool = hidden
        
        # Store signature and resolve type hints
        self.signature: inspect.Signature = inspect.signature(func)
        try:
            # get_type_hints resolves string annotations to actual types
            self.type_hints: dict[str, type] = get_type_hints(func)
        except Exception as e:
            # If get_type_hints fails, we'll fall back to the signature
            LOGGER.warning(f"Could not resolve type hints for {func.__name__}: {e}")
            self.type_hints = {}

        # Load parameter configurations from decorator
        params_config: dict[str, dict[str, Any]] = getattr(func, '_listener_command_params', {})
        
        kind_mapping = {
            inspect.Parameter.POSITIONAL_ONLY: ParameterType.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD: ParameterType.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL: ParameterType.VAR_POSITIONAL,
            inspect.Parameter.KEYWORD_ONLY: ParameterType.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD: ParameterType.VAR_KEYWORD
        }

        self.parameters: list[Parameter] = []
        self.parameters_map: dict[str, Parameter] = {}
        self.arg_mappings: dict[str, str] = {}
        
        doc_params: dict[str, str | None] = {p.arg_name: p.description for p in doc.params}

        # Process parameters
        # Skip 'self' (if bound) and 'ctx'
        sig_params: list[inspect.Parameter] = list(self.signature.parameters.values())
        if sig_params and sig_params[0].name == 'self':
            __ = sig_params.pop(0)
        if sig_params and (sig_params[0].name == 'g_ctx' or sig_params[0].annotation == GlobalContext):
            __ = sig_params.pop(0)
        if sig_params and (sig_params[0].name in ['event', 'ctx'] or sig_params[0].annotation == CommandEvent or 'Event' in str(sig_params[0].annotation)):
            __ = sig_params.pop(0)
            
        for param in sig_params:
            param_config: dict[str, Any] = params_config.get(param.name, {})
            
            # Resolve aliases
            param_aliases: list[str] = param_config.get('aliases', []) or []
            if isinstance(param_aliases, str):
                param_aliases = [param_aliases]
            
            display_name: str = param_config.get('display_name', None) or param.name

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
            if isinstance(origin, UnionType):
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

            param_kind = kind_mapping[param.kind]

            # Create Parameter object
            p = Parameter(
                name=param.name,
                display_name=display_name,
                raw_annotation=raw_annotation,
                annotation=annotation,
                converter=converter,
                is_optional=is_optional,
                default=param.default,
                aliases=param_aliases,
                greedy=param_config.get('greedy', None) or False,
                hidden=param_config.get('hidden', None) or False,
                kind=param_kind,
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
            for alias in param_aliases:
                self.arg_mappings[alias] = param.name

    @override
    async def check_for_match(self, event: BaseEvent) -> bool:
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

    async def _run_verification(self, g_ctx: GlobalContext, event: CommandEvent, *args: Any, **kwargs: Any):
        # Run verifier if present
        if self.verifier:
            try:
                verify_result = self.verifier(g_ctx, event, *args, **kwargs)
                if asyncio.iscoroutine(verify_result):
                    verify_result = await verify_result
                
                if isinstance(verify_result, str):
                    raise VerificationFailure(verify_result)
                if verify_result is False:
                    raise VerificationFailure(f"Verification failed for command '{self.name}'")
            except VerificationFailure:
                raise
            except Exception as e:
                LOGGER.error(f"Verification raised an error: {e}")
                raise VerificationFailure("An unknown error occurred")

    async def _convert_argument(self, ctx: CommandEvent, value: Any, param: Parameter, g_ctx: GlobalContext | None = None) -> Any:
        if value is None:
            return value
        if param.annotation == inspect.Parameter.empty:
            return value
            
        conv_obj = param.converter

        if type(value) == type(conv_obj):
            return value

        if value == True:
            raise EmptyFlagValueError(param)

        if isinstance(conv_obj, BaseConverter):
            try:
                # Inspect the convert method's signature to determine parameters
                convert_method: Callable[..., Any | Awaitable[Any]] = getattr(conv_obj, 'convert')
                
                result = convert_method(g_ctx, ctx, value)
                if asyncio.iscoroutine(result):
                    return await result
                return result
            except ArgumentConversionError as e:
                raise ArgumentConversionError(e.message, value, param)
            except Exception as e:
                raise ArgumentConversionError(f"An error occurred while converting the argument: {e}", value, param, e)
        elif conv_obj is bool:
            if isinstance(value, bool):
                return value
            elif isinstance(value, str):
                if value.lower() in ('true', 'yes', '1', 'on'):
                    return True
                elif value.lower() in ('false', 'no', '0', 'off'):
                    return False
                else:
                    raise ArgumentConversionError("Expected a boolean", value, param)
            else:
                raise ArgumentConversionError("Expected a boolean", value, param)
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

    async def _parse_arguments(self, ctx: CommandEvent, g_ctx: GlobalContext) -> tuple[set[str], dict[str, Any]]:
        kwargs: dict[str, Any] = {}
        
        # Separate positional args and flags from ctx.args
        positional_args: list[str] = []
        named_args: dict[str, str | None] = {}
        parsed_args = ctx.parsed_args
        
        specified_params: set[str] = set()

        # Check if we have a VAR_KEYWORD parameter
        has_var_keyword = any(p.kind == ParameterType.VAR_KEYWORD for p in self.parameters)

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
            if param.kind == ParameterType.VAR_POSITIONAL:
                for arg in positional_args[positional_index:]:
                    converted = await self._convert_argument(ctx, arg, param, g_ctx)
                    kwargs[param.name] = converted
                    specified_params.add(param.name)
                positional_index = len(positional_args)
                continue
            
            # 2. Handle VAR_KEYWORD (**kwargs)
            if param.kind == ParameterType.VAR_KEYWORD:
                for name, value in list(named_args.items()):
                    converted = await self._convert_argument(ctx, value, param, g_ctx)
                    kwargs[name] = converted
                    specified_params.add(param.name)
                    del named_args[name]
                continue
            
            # 3. Handle specific argument (Positional, Keyword, or both)
            
            # Check if provided by name
            if param_name in named_args:
                val = named_args[param_name]
                del named_args[param_name]
                
                converted = await self._convert_argument(ctx, val, param, g_ctx)
                kwargs[param_name] = converted
                specified_params.add(param.name)
                continue
            
            # If KEYWORD_ONLY and not in named_args (checked above)
            if param.kind == ParameterType.KEYWORD_ONLY:
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
                
                if param.greedy and positional_index < len(positional_args):
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
                # param.kind == ParameterKind.POSITIONAL_ONLY:
                kwargs[param_name] = converted
                specified_params.add(param.name)
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

        return specified_params, kwargs

    @override
    async def invoke(self, global_ctx: GlobalContext, event: CommandEvent, *args: Any, **kwargs: Any) -> None:
        await self._run_checks(global_ctx, event)
        await self._check_cooldown(event)
        if self.parameters:
            specified_params, parsed_kwargs = await self._parse_arguments(event, global_ctx)
            event.specified_parameters = specified_params
            kwargs = {**kwargs, **parsed_kwargs}
        await self._run_verification(global_ctx, event, *args, **kwargs)
        await self._run_func(global_ctx, event, *args, **kwargs)

    def get_usage_text(self, prefix: str, invoked_name: str | None = None):
        if not invoked_name:
            invoked_name = self.name
        nm_out = [f"{prefix}{invoked_name}"]
        
        for param in self.parameters:
            if param.hidden:
                continue
            param_str = param.get_parameter_display()
            nm_out.append(param_str)
        
        return " ".join(nm_out)
        
    def get_help_text(self, prefix: str, invoked_name: str | None = None):
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
            restriction_list: list[str] = []
            for check in self.checks:
                restriction_list.append(check.title or check.short_help or check.help or check.__qualname__)
            restrictions = f"Limited to: {', '.join(restriction_list).capitalize()}"
        cooldowns = ""
        if self.cooldown:
            cd_buckets = ', '.join([b.name.lower() for b in self.cooldown.bucket])
            if self.cooldown.rate == 1:
                cooldowns = f"Cooldown: {self.cooldown.per}s ({cd_buckets})"
            else:
                cooldowns = f"Cooldown: {self.cooldown.rate}x/{self.cooldown.per}s ({cd_buckets})"

        response = strjoin(' – ', name_and_usage, description, restrictions, aliases, cooldowns)
        return response

class CommandResponse(NamedTuple):
    text: str
    args: tuple[Any]
    kwargs: dict[str, Any]

class CommandExecutionResult(NamedTuple):
    responses: list[CommandResponse]
    error: Exception | None
    
class CommandDispatchResult(NamedTuple):
    listener: "CommandListener | None"
    error: Exception | None




@dataclass
class Flag:
    name: str
    value: str | None = None

    @override
    def __repr__(self) -> str:
        return f"Flag({self.name}, {self.value})"
    
