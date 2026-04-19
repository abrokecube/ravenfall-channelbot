from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, override

from utils.strutils import strjoin

from .enums import ParameterType

if TYPE_CHECKING:
    from bot.core.components import GlobalContext

    from .events import CommandEvent
    from .listeners import CommandListener

LOGGER = logging.getLogger(__name__)

EVENT_CATEGORY_COMMAND = "command"


class BaseConverter:
    """Base converter class for command argument conversion.

    To display a custom error message when conversion fails,
    raise command_exceptions.ArgumentConversionError in the convert method.
    """

    title: str = ""
    short_help: str = ""
    help: str = ""

    async def convert(
        self,
        g_ctx: GlobalContext,  # pyright: ignore[reportUnusedParameter]
        event: CommandEvent,  # pyright: ignore[reportUnusedParameter]
        arg: str,  # pyright: ignore[reportUnusedParameter]
    ) -> object:
        """Convert a string argument to the desired type."""
        raise NotImplementedError

    @classmethod
    async def cls_convert(
        cls,
        g_ctx: GlobalContext,  # pyright: ignore[reportUnusedParameter]
        event: CommandEvent,  # pyright: ignore[reportUnusedParameter]
        arg: str,  # pyright: ignore[reportUnusedParameter]
    ) -> object:
        """Convert a string argument to the desired type."""
        raise NotImplementedError


DELIMETERS = ("=", ":")
RE_FLAG = re.compile(r"[-a-zA-Z]{2}[a-zA-Z]+[:=]+.+|-[a-zA-Z]\b|--[a-zA-Z_]+\b")


class CommandArgs:
    """Arguments passed to a command.

    Includes parsing of flags and grouping of consecutive non-flag arguments.
    """

    def __init__(self, text: str):
        self.text: str = text

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
                if i > 0 and self.text[i - 1] == "\\":
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
                    args.append("".join(current))
                    current = []
            else:
                current.append(char)

            i += 1

        if current:
            args.append("".join(current))

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
                flag_name: str = arg.lstrip("-")
                flag_value: str | None = None
                if has_delimiter and delimiter_char and delimiter_char in flag_name:
                    flag_name, flag_value = flag_name.split(delimiter_char, 1)
                if (
                    isinstance(flag_value, str)
                    and flag_value[0] == '"'
                    and flag_value[-1] == '"'
                ):
                    flag_value = flag_value[1:-1]
                flag = Flag(flag_name, flag_value)
                self.flags.append(flag)
                self.args.append(flag)
            else:
                arg_trim = arg
                if is_quoted:
                    arg_trim = arg[1:-1]
                self.args.append(arg_trim)

        # Build grouped_args by joining consecutive non-flag args with spaces,
        # using flags as separators (flags are not included in grouped_args)
        grouped: list[str] = []
        current_group: list[str] = []
        for item in self.args:
            if isinstance(item, Flag):
                if current_group:
                    grouped.append(" ".join(current_group))
                    current_group = []
            else:
                current_group.append(item)
        if current_group:
            grouped.append(" ".join(current_group))
        self.grouped_args = grouped


@dataclass
class Parameter:
    """Command parameter definition."""

    name: str
    display_name: str
    raw_annotation: object
    annotation: object
    converter: BaseConverter | type
    kind: ParameterType
    default: object = inspect.Parameter.empty
    aliases: list[str] = field(default_factory=list)
    greedy: bool = False
    hidden: bool = False
    is_optional: bool = False
    type_title: str | None = None
    type_short_help: str | None = None
    type_help: str | None = None
    help_text: str | None = None
    command: CommandListener | None = None
    regex: str | re.Pattern[str] | re.Pattern[bytes] | None = None
    _regex_compiled: re.Pattern[str] | re.Pattern[bytes] | None = None

    def __post_init__(self):
        if isinstance(self.regex, str):
            self._regex_compiled = re.compile(self.regex)
        elif isinstance(self.regex, re.Pattern):
            self._regex_compiled = self.regex

    def get_parameter_display(self, invoked_name: str | None = None) -> str:
        """Help text helper."""
        param_str = invoked_name or self.display_name
        if self.type_title:
            param_str += f": {self.type_title}"
        if self.kind == ParameterType.KEYWORD_ONLY:
            param_str = f"(-{param_str})" if len(param_str) == 1 else f"(--{param_str})"
        elif self.is_optional:
            param_str = f"({param_str})"
        else:
            param_str = f"<{param_str}>"
        return param_str

    def get_help_text(self, invoked_name: str | None = None) -> str:
        """Help text helper."""
        param_aliases = self.aliases[:]

        if invoked_name and invoked_name in param_aliases:
            param_aliases.remove(invoked_name)
            param_aliases.append(self.name)
        if self.display_name != self.name and invoked_name == self.name:
            param_aliases.append(self.display_name)
        param_aliases.sort()

        out_str: list[str] = []
        param_str = self.get_parameter_display(invoked_name)
        out_str.append(param_str)
        help_text = self.help_text
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
        if self.default != inspect.Parameter.empty and self.default:
            out_str.append(f"Default: {self.default}")
        if type_help:
            out_str.append(f"Expects {self.type_title}: {type_help}")
        if param_aliases:
            out_str.append(f"Aliases: {', '.join(param_aliases)}")

        response = strjoin(" – ", *out_str)  # noqa: RUF001
        return response  # noqa: RET504


class CommandResponse(NamedTuple):
    """Response from a command execution."""

    text: str
    args: tuple[object, ...]
    kwargs: dict[str, object]


class CommandExecutionResult(NamedTuple):
    """Result from a command execution."""

    responses: list[CommandResponse]
    error: Exception | None


class CommandDispatchResult(NamedTuple):
    """Result from a command dispatch."""

    listener: CommandListener | None
    error: Exception | None


@dataclass
class Flag:
    """Parsed flag from command arguments."""

    name: str
    value: str | None = None

    @override
    def __repr__(self) -> str:
        return f"Flag({self.name}, {self.value})"
