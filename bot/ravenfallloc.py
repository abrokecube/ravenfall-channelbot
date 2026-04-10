from __future__ import annotations

import logging
import os
import random
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast, override

from ruamel import yaml as yamllib
from ruamel.yaml import YAML

if TYPE_CHECKING:
    from re import Pattern

# Configure logging
logger = logging.getLogger(__name__)

# Compiled regex patterns for string matching and translation
MATCH_DEF_TOKENIZER: Pattern[str] = re.compile(
    r"{(?P<given>[a-zA-Z_0-9]+)}|{{(?P<parsed>[a-zA-Z_0-9]+(:((?:(?!}})).)+)?)}}|(?P<nothing>[^{}]*)"
)
TRANSLATE_TOKENIZER: Pattern[str] = re.compile(
    r"{(?P<given>[a-zA-Z_0-9]+)}|{{(?P<eval>((?:(?!}})).)+)}}|(?P<nothing>[^{}]*)"
)
FSTRINGS: Pattern[str] = re.compile(r"{([a-zA-Z_0-9]+)}")


class StringArgType(Enum):
    """String argument type."""

    GIVEN = 0
    PARSED = 1


class StringArg(NamedTuple):
    """String argument."""

    name: str
    arg_type: StringArgType


@dataclass
class TranslatedString:
    """Container for translated string variations."""

    key: str
    strings: list[str]


def pl(number: float, singular: str, plural: str):
    """Pluralization helper function."""
    if number == 1:
        return singular
    return plural


def unping(in_str: str):
    """Prevent pings in messages by adding invisible characters."""
    out: list[str] = []
    for word in in_str.split():
        if len(word) < 3:
            out.append(word)
        elif len(word) < 6:
            out.append(f"\U000e0000{word}")
        else:
            a = word[:-2]
            b = word[-2:]
            out.append(f"{a}\U000e0000{b}")
    return " ".join(out)


def pickrand[T](*args: T) -> T:
    """Pick a random argument."""
    return random.choice(args)


def to_str(obj: object) -> str:
    """Converts an object to a string."""
    if isinstance(obj, float):
        if obj.is_integer():
            obj = int(obj)
        obj = f"{obj:,}"
    elif isinstance(obj, int):
        obj = f"{obj:,}"
    else:
        obj = str(obj)
    return obj


class RavenfallLocalization:
    """Handles string localization and translation for Ravenfall bot."""

    def __init__(
        self, definitions_path: str = "definitions.yaml", strings_path: str | None = None
    ):
        """Initialize the localization system with paths to definition and string files."""
        self.definitions_path: str = definitions_path
        self.strings_path: str | None = strings_path

        # Initialize storage
        self.strings: list[Matcher] = []
        self.strings_dict: dict[str, Matcher] = {}
        self.simple_matches: dict[str, Matcher] = {}
        self.regex_matches: list[Matcher] = []
        self.translated_strings: dict[str, TranslatedString] = {}

        # Load definitions and translations
        self.load_definitions()
        self.load_translations()

    def load_definitions(self):
        """Load string definitions from YAML file."""
        yaml = YAML()
        with Path(self.definitions_path).open(encoding="utf-8") as f:
            defs: dict[str, str | yamllib.CommentedSeq] = yaml.load(f)
            if not isinstance(defs, dict):
                defs = {}
            else:
                defs = cast(
                    "dict[str, str | yamllib.CommentedSeq]", defs
                )  # satisfy pyright

        self.strings.clear()
        self.simple_matches.clear()
        self.regex_matches.clear()
        self.strings_dict.clear()

        # Load string definitions
        for key, match_str in defs.items():
            if isinstance(match_str, yamllib.CommentedSeq):
                for sub_match_str in match_str:
                    self.strings.append(Matcher(key, sub_match_str))
            else:
                self.strings.append(Matcher(key, match_str))

        # Index strings for faster lookup
        for matcher in self.strings:
            if matcher.key in self.simple_matches and matcher.key != "-":
                raise Exception(f"Duplicate key '{matcher.key}'!")
            if matcher.regex is None:
                self.simple_matches[matcher.match_string] = matcher
            else:
                self.regex_matches.append(matcher)

        # Create a dictionary for quick lookup by key
        for a in self.strings:
            self.strings_dict[a.key] = a

    def load_translations(self):
        """Load string translations from YAML file."""
        yaml = YAML()
        if self.strings_path is None:
            logger.debug("No strings file was loaded")
            return
        if not Path(self.strings_path).exists():
            logger.warning(f"Strings file not found: {self.strings_path}")
            return

        with Path(self.strings_path).open(encoding="utf-8") as f:
            defs: dict[str, str | yamllib.CommentedSeq] = yaml.load(f)
            if not isinstance(defs, dict):
                defs = {}
            else:
                defs = cast(
                    "dict[str, str | yamllib.CommentedSeq]", defs
                )  # satisfy pyright

        self.translated_strings.clear()

        for key, trans_str in defs.items():
            strs = []
            if isinstance(trans_str, yamllib.CommentedSeq):
                strs = list(trans_str)
            else:
                strs = [trans_str]
            self.translated_strings[key] = TranslatedString(key, strs)

    def _fill_args(
        self,
        in_str: str,
        in_args: list[str | float],
        named_args: dict[str, str | float] | None = None,
    ) -> str:
        """Fill in arguments in a format string."""
        if not named_args:
            named_args = {}
        expl_args: dict[str, str | float] = {}
        results: list[str] = FSTRINGS.findall(in_str)
        for a in results:
            expl_args[a] = ""
        for argname, argvalue in zip(expl_args, in_args, strict=False):
            expl_args[argname] = to_str(argvalue)
        expl_args.update(named_args)
        return in_str.format_map(expl_args)

    def identify_string(self, in_str: str):
        """Get a string identifier key from an input format string."""
        if in_str in self.simple_matches:
            return self.simple_matches[in_str]
        for m in self.regex_matches:
            if m.regex and m.regex.match(in_str):
                return m
        return None

    def translate_string(
        self,
        in_str: str,
        in_args: list[str | int | float],
        match: Matcher | None = None,
        additional_args: dict[str, str | float] | None = None,
    ) -> str:
        """Translate a string using the loaded definitions and translations.

        Args:
            in_str: The input string to translate
            in_args: list of arguments to use for formatting
            match: Optional pre-matched string definition
            additional_args: Additional arguments to use for formatting

        Returns:
            str: The translated string
        """
        if not additional_args:
            additional_args = {}
        if match is None:
            matched = self.identify_string(in_str)
            if matched:
                logger.debug(f"Matched key {matched.key}")
                key = matched.key
            else:
                logger.warning(f"🚨🚨 No matched key for string: {in_str}")
                return f"{self._fill_args(in_str, in_args, additional_args)}"
        else:
            logger.debug(f"Using key {match.key}")
            matched = match
            key = match.key

        translation = None
        if key in self.translated_strings:
            translation = self.translated_strings[key]

        if translation is None:
            logger.info(f"No translation for {key}")
            return self._fill_args(in_str, in_args)

        if not translation.strings:
            return ""

        translation_string = random.choice(translation.strings)
        return matched.translate(translation_string, in_str, in_args, additional_args)

    def s(self, in_str: str, **kwargs: str | float) -> str:
        """Shorthand method to get a translated string with named arguments.

        Args:
            in_str: The input string to translate
            **kwargs: Named arguments for formatting

        Returns:
            str: The translated string
        """
        return self.translate_string(in_str, [], additional_args=kwargs)

    def getstr(self, key: str, args: dict[str, str] | None = None) -> str:
        """Get a translated string by key with the given arguments.

        Args:
            key: The key of the string to retrieve
            args: dictionary of arguments to format the string with

        Returns:
            str: The translated and formatted string

        Raises:
            ValueError: If the key is not found or no translation is available
        """
        if args is None:
            args = {}

        if key not in self.strings_dict:
            msg = f"String key not found: {key}"
            raise ValueError(msg)

        matcher = self.strings_dict[key]
        default_str = ""
        trans_str = ""

        logger.debug(f"Matched key {key}")

        # Get translated string if available
        if key in self.translated_strings:
            if not self.translated_strings[key].strings:
                return ""
            trans_str = random.choice(self.translated_strings[key].strings)
        else:
            logger.warning(f"No translation for {key}")

        # Validate we have at least one string to work with
        if not default_str and not trans_str:
            msg = f"No string found for key: {key}"
            raise ValueError(msg)

        # Use default string if no translation is available
        if not trans_str:
            trans_str = default_str

        return matcher.translate(
            trans_str, default_str, cast("dict[str, str | int | float]", args)
        )


class Matcher:
    """String matcher."""

    def __init__(self, key: str, match_string: str = ""):
        self.key: str = key
        self.match_string: str = match_string
        self.arguments: list[StringArg] = []
        regex_str_build: list[str] = ["^"]
        orig_str_build: list[str] = []
        has_regex = False
        for mo in MATCH_DEF_TOKENIZER.finditer(match_string):
            kind = mo.lastgroup
            value: str = mo.groupdict().get(kind or "", "")
            match kind:
                case "nothing":
                    regex_str_build.append(re.escape(value))
                    orig_str_build.append(value)
                case "parsed":
                    name: str = value
                    matcher = ".+"
                    split = value.split(":", 1)
                    if len(split) == 2:
                        name, matcher = split
                    self.arguments.append(StringArg(name, StringArgType.PARSED))
                    regex_str_build.append(f"({matcher})")
                    orig_str_build.append(name)
                    has_regex = True
                case "given":
                    self.arguments.append(StringArg(value, StringArgType.GIVEN))
                    regex_str_build.append(re.escape(value))
                    orig_str_build.append(value)
                case _:
                    logger.error("Unexpected match group in string pattern")
        self.regex: re.Pattern[str] | None = None
        if has_regex:
            regex_str_build.append("$")
            self.regex = re.compile("".join(regex_str_build))
            self.match_string = "".join(orig_str_build)
            # print(self.regex)
            # print(self.match_string)

    def extract_args(
        self, rf_string: str, rf_args: list[str | int | float]
    ) -> dict[str, str | int | float]:
        # expl_args = [x for x in self.arguments if x.arg_type == StringArgType.GIVEN]
        expl_args: dict[str, None] = {}
        for a in FSTRINGS.findall(rf_string):
            # ordered set
            expl_args[a] = None
        impl_args = [x.name for x in self.arguments if x.arg_type == StringArgType.PARSED]
        mapped_args: dict[str, str | int | float] = {}

        if len(impl_args) > 0:
            if not self.regex:
                msg = "Matcher has no regex pattern"
                raise ValueError(msg)
            groups: list[str] = self.regex.findall(rf_string)
            if len(groups) == 1:
                # if isinstance(groups[0], str):
                mapped_args[impl_args[0]] = groups[0]
                # else:
                #     for idx, g in enumerate(groups[0]):
                #         mapped_args[impl_args[idx]] = g
            else:
                msg = "Input string may not match this matcher"
                raise ValueError(msg)

        for idx, argname in enumerate(expl_args):
            mapped_args[argname] = rf_args[idx]

        return mapped_args

    def translate(
        self,
        trans_string: str,
        rf_string: str,
        rf_args: list[str | int | float] | dict[str, str | int | float],
        additional_args: dict[str, str | float] | None = None,
    ) -> str:
        if not additional_args:
            additional_args = {}
        if isinstance(rf_args, dict):
            mapped_args = rf_args
        else:
            mapped_args = self.extract_args(rf_string, rf_args)
        mapped_args.update(additional_args)

        def fill(string: str) -> str:
            return self.translate(string, rf_string, rf_args, additional_args)

        str_a = trans_string
        str_b = ""
        eval_globals: dict[str, object] = {}
        eval_globals.update(mapped_args)
        eval_globals.update(
            {
                "pl": pl,
                "llb": "{{",
                "rrb": "}}",
                "pick": pickrand,
                "unping": unping,
                "fill": fill,
            }
        )
        while str_a != str_b:
            string_build: list[str] = []
            for mo in TRANSLATE_TOKENIZER.finditer(str_a):
                kind = mo.lastgroup
                if kind is None:
                    continue
                value = mo.groupdict()[kind]
                match kind:
                    case "nothing":
                        string_build.append(value)
                    case "given":
                        if value in mapped_args:
                            a = to_str(mapped_args[value])
                            string_build.append(a)
                        else:
                            string_build.append(value)
                    case "eval":
                        try:
                            logger.debug(f"Evaluating expression: {value}")
                            eval_out: Any = eval(value, eval_globals)  # noqa: S307
                        except Exception:
                            logger.exception(
                                f"Evaluation failed for expression '{value}'"
                            )
                            eval_out = "(?)"
                        string_build.append(to_str(eval_out))
                    case _:
                        logger.warning(f"Unknown token type: {kind}")
            str_b = "".join(string_build)

            str_a, str_b = (str_b, str_a)

        return str_b

    @override
    def __repr__(self):
        return f"Match({self.key})"
