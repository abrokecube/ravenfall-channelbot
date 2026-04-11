from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from ruamel import yaml as ruamel_yaml
from ruamel.yaml import YAML

from bot.integrations.ravenfall.events import RavenfallEvent

from . import models as md

if TYPE_CHECKING:
    import io

type RavenfallInstanceEventHook = Callable[[RavenfallEvent], Awaitable[None]]
RavenfallConfig = md.RavenfallConfig

LOGGER = logging.getLogger(__name__)


class RegexMatcher(NamedTuple):
    """Ravenfall string regex matcher."""

    pattern: re.Pattern[str]
    identifier: str


@dataclass
class Match:
    """Matched Ravenfall string."""

    identifier: str | None
    _args: dict[str, str | float]

    def get_arg(self, key: str):
        """Get a format string argument's value."""
        return self._args.get(key)


MATCH_DEF_TOKENIZER: re.Pattern[str] = re.compile(
    r"{(?P<given>[a-zA-Z_0-9]+)}|{{(?P<parsed>[a-zA-Z_0-9]+(:((?:(?!}})).)+)?)}}|(?P<nothing>[^{}]*)"
)
FSTRINGS: re.Pattern[str] = re.compile(r"{([a-zA-Z_0-9]+)}")


def _parse_matcher_string(match_string: str) -> re.Pattern[str]:
    regex_str_build: list[str] = ["^"]
    for mo in MATCH_DEF_TOKENIZER.finditer(match_string):
        kind = mo.lastgroup
        value: str = mo.groupdict().get(kind or "", "")
        match kind:
            case "nothing":
                regex_str_build.append(re.escape(value))
            case "parsed":
                name: str = value
                matcher = ".+"
                split = value.split(":", 1)
                if len(split) == 2:  # noqa: PLR2004
                    name, matcher = split
                regex_str_build.append(f"(?P<{name}>{matcher})")
            case "given":
                regex_str_build.append(re.escape("{" + value + "}"))
            case _:
                LOGGER.error("Unexpected match group in string pattern")
    regex_str_build.append("$")
    return re.compile("".join(regex_str_build))


class RavenfallMatcher:
    """Matches incoming format strings from Ravenfall."""

    def __init__(self, definitions_text_buf: io.TextIOBase):
        self._string_matchers: dict[str, str] = {}
        self._regex_matchers: list[RegexMatcher] = []

        yaml = YAML()
        definitions_yaml: Any = yaml.load(definitions_text_buf)
        defs_dict: dict[str, Any] = {}
        if not isinstance(definitions_yaml, dict):
            defs_dict = {}
        else:
            defs_dict = cast("dict[Any, Any]", definitions_yaml)

        for key, match_str in defs_dict.items():
            if isinstance(match_str, ruamel_yaml.CommentedSeq):
                for sub_match_str in match_str:
                    if isinstance(sub_match_str, str):
                        self._add_matcher(key, sub_match_str)
                    else:
                        LOGGER.error(f"Invalid value '{sub_match_str}' for '{key}'")
            elif isinstance(match_str, str):
                self._add_matcher(key, match_str)
            else:
                LOGGER.error(f"Invalid value '{match_str}' for '{key}'")

    def _add_matcher(self, key: str, match_str: str):
        if "{{" in match_str:
            self._regex_matchers.append(
                RegexMatcher(_parse_matcher_string(match_str), key)
            )
        else:
            self._string_matchers[match_str] = key

    def match_string(self, format_str: str, args: list[Any]) -> Match:
        """Matches a given format string."""
        args = args.copy()
        str_identifier: str | None = None
        mapped_args: dict[str, Any] = {}
        if format_str in self._string_matchers:
            str_identifier = self._string_matchers[format_str]
        else:
            re_match: re.Match[str] | None = None
            for regex_matcher in self._regex_matchers:
                re_match = regex_matcher.pattern.match(format_str)
                if re_match is not None:
                    str_identifier = regex_matcher.identifier
                    break
            if re_match is not None:
                mapped_args = re_match.groupdict()

        for a in FSTRINGS.finditer(format_str):
            mapped_args[a.group(1)] = args.pop(0)

        return Match(str_identifier, mapped_args)
