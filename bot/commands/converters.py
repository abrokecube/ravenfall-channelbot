from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple, Any, List, Dict
if TYPE_CHECKING:
    from events import CommandEvent
from utils.utils import strjoin
from .exceptions import ArgumentConversionError

class BaseConverter:
    """To display a custom error message when conversion fails,
    raise command_exceptions.ArgumentConversionError in the convert method."""
    title: str = None
    short_help: str = None
    help: str = None

    @classmethod
    async def convert(cls, event: CommandEvent, arg: str) -> Any:
        raise NotImplementedError

class Choice(BaseConverter):
    def __init__(self, definition: List[str] | Dict[str, List[str]], title=None, case_sensitive=False):
        super().__init__()
        string_map = {}
        choices = []
        if isinstance(definition, list):
            if case_sensitive:
                string_map = {x: x for x in definition}
            else:
                string_map = {x.lower(): x for x in definition}
            choices = definition
        elif isinstance(definition, dict):
            choices = list(definition.keys())
            if case_sensitive:
                string_map = {x: x for x in choices}
                for k, v in definition.items():
                    string_map.update({x: k for x in v})
            else:
                string_map = {x.lower(): x for x in choices}
                for k, v in definition.items():
                    string_map.update({x.lower(): k for x in v})
        else:
            raise TypeError()
        
        if title:
            self.title = title
        else:
            self.title = f"Choice ({len(choices)})"
        self.short_help = f"One of the following: {strjoin(', ', *choices, before_end='or ', include_conn_char_before_end=True)}"
        self.help = self.short_help
        self.case_sensitive = case_sensitive
        self.string_map = string_map
        
    async def convert(self, ctx: CommandEvent, arg: str) -> str:
        if not arg in self.string_map:
            raise ArgumentConversionError(f"Choice '{arg}' is not a valid option. Valid choices: {self.short_help}")
        return self.string_map[arg]

import re
import glob 

class Regex(BaseConverter):
    title = "Regex"
    short_help = "A python regular expression"
    help = "A python regular expression"
    
    async def convert(ctx: CommandEvent, arg: str) -> re.Pattern:
        try:
            return re.compile(arg)
        except Exception as e:
            raise ArgumentConversionError("Couldn't compile regex")

class Glob(BaseConverter):
    title = "Glob"
    short_help = "A glob pattern"
    help = "A glob pattern"
    
    async def convert(ctx: CommandEvent, arg: str) -> re.Pattern:
        try:
            return re.compile(glob.translate(arg))
        except Exception as e:
            raise ArgumentConversionError("Couldn't compile glob expression")
        
class RangeInt(BaseConverter):
    def __init__(self, min_: int | None, max_: int | None):
        super().__init__()
        self.min = min_
        self.max = max_
        if min_ is not None and max_ is not None:
            self.title = f"Number ({min_}-{max_})"
            self.short_help = f"An integer in the range {min_} to {max_}"
            self.help = f"A whole number in the range {min_} to {max_}"
        elif min_ is None and max_ is not None:
            self.title = f"Number ({max_}-)"
            self.short_help = f"An integer less than or equal to {max_}"
            self.help = f"A whole number less than or equal to {max_}"
        elif min_ is not None and max_ is None:
            self.title = f"Number ({min_}+)"
            self.short_help = f"An integer greater than or equal to {min_}"
            self.help = f"A whole number greater than or equal to {min_}"
        else:
            raise ValueError("min_ or max_ need to be a number")
        
    async def convert(self, ctx: CommandEvent, arg: str) -> int:
        try:
            number = int(arg)
        except ValueError as e:
            raise ArgumentConversionError("Expected an integer")
        
        if self.max is not None and number > self.max:
            raise ArgumentConversionError(f"Number is out of range! Maximum value: {self.max}")
        if self.min is not None and number < self.min:
            raise ArgumentConversionError(f"Number is out of range! Minimum value: {self.min}")
    
        return number

class RangeFloat(BaseConverter):
    def __init__(self, min_: float | None, max_: float | None):
        super().__init__()
        self.min = min_
        self.max = max_
        if min_ is not None and max_ is not None:
            self.title = f"Decimal ({min_}-{max_})"
            self.short_help = f"A decimal number in the range {min_} to {max_}"
            self.help = f"A decimal number in the range {min_} to {max_}"
        elif min_ is None and max_ is not None:
            self.title = f"Decimal ({max_}+)"
            self.short_help = f"A decimal number less than or equal to {max_}"
            self.help = f"A decimal number less than or equal to {max_}"
        elif min_ is not None and max_ is None:
            self.title = f"Decimal ({min_}-)"
            self.short_help = f"A decimal number greater than or equal to {min_}"
            self.help = f"A decimal number greater than or equal to {min_}"
        else:
            raise ValueError("min_ or max_ need to be a number")
        
    async def convert(self, ctx: CommandEvent, arg: str) -> float:
        try:
            number = int(arg)
        except ValueError as e:
            raise ArgumentConversionError("Expected a number")
        
        if self.max is not None and number > self.max:
            raise ArgumentConversionError(f"Number is out of range! Maximum value: {self.max}")
        if self.min is not None and number < self.min:
            raise ArgumentConversionError(f"Number is out of range! Minimum value: {self.min}")

        return number
