# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple, Any, override
from collections.abc import Collection
from .enums import EventCategory, EventSource, ParameterType
import inspect
from dataclasses import dataclass, field
from utils.strutils import strjoin
import re

if TYPE_CHECKING:
    from .listeners import CommandListener
    from .converters import BaseConverter

class MetaFilter(NamedTuple):
    categories: Collection[EventCategory]
    invert_categories: bool  # only include the listed categories
    platforms: Collection[EventSource]
    invert_platforms: bool  # only include the listed platforms

class ChatRoomCapabilities(NamedTuple):
    multiline: bool
    max_message_length: int

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

@dataclass
class Flag:
    name: str
    value: str | None = None

    @override
    def __repr__(self) -> str:
        return f"Flag({self.name}, {self.value})"
    
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

