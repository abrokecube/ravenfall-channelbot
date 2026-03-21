from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import (
    Any,
    NamedTuple,
    override,
)

from bot.core.components import GlobalContext
from utils.strutils import strjoin

from .enums import ParameterType
from .events import CommandEvent
from .listeners import CommandListener

LOGGER = logging.getLogger(__name__)


class BaseConverter:
    """To display a custom error message when conversion fails,
    raise command_exceptions.ArgumentConversionError in the convert method."""

    title: str = ""
    short_help: str = ""
    help: str = ""

    async def convert(self, g_ctx: GlobalContext, event: CommandEvent, arg: str) -> Any:  # pyright: ignore[reportUnusedParameter, reportAny, reportExplicitAny]
        raise NotImplementedError


DELIMETERS = ("=", ":")
RE_FLAG = re.compile(r"[-a-zA-Z]{2}[a-zA-Z]+[:=]+.+|-[a-zA-Z]\b|--[a-zA-Z_]+\b")


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
                    grouped.append(" ".join(current_group))
                    current_group = []
            else:
                current_group.append(item)
        if current_group:
            grouped.append(" ".join(current_group))
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
            param_str = f"(-{param_str})" if len(param_str) == 1 else f"(--{param_str})"
        elif self.is_optional:
            param_str = f"({param_str})"
        else:
            param_str = f"<{param_str}>"
        return param_str

    def get_help_text(self, invoked_name: str | None = None) -> str:
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
        if self.default != inspect.Parameter.empty and self.default:
            out_str.append(f"Default: {self.default}")
        if type_help:
            out_str.append(f"Expects {self.type_title}: {type_help}")
        if param_aliases:
            out_str.append(f"Aliases: {', '.join(param_aliases)}")

        response = strjoin(" – ", *out_str)
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
