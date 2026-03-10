from typing import NamedTuple, override, cast, Any
from enum import Enum
import re
from re import Pattern
import os
import logging
from ruamel.yaml import YAML
from ruamel import yaml as yamllib
from dataclasses import dataclass
import random

# Configure logging
logger = logging.getLogger(__name__)

# Compiled regex patterns for string matching and translation
MATCH_DEF_TOKENIZER: Pattern[str] = re.compile(r"{(?P<given>[a-zA-Z_0-9]+)}|{{(?P<parsed>[a-zA-Z_0-9]+(:((?:(?!}})).)+)?)}}|(?P<nothing>[^{}]*)")
TRANSLATE_TOKENIZER: Pattern[str] = re.compile(r'{(?P<given>[a-zA-Z_0-9]+)}|{{(?P<eval>((?:(?!}})).)+)}}|(?P<nothing>[^{}]*)')
FSTRINGS: Pattern[str] = re.compile(r'{([a-zA-Z_0-9]+)}')

class StringArgType(Enum):
    GIVEN = 0
    PARSED = 1

class StringArg(NamedTuple):
    name: str
    arg_type: StringArgType

@dataclass
class TranslatedString():
    """Container for translated string variations."""
    key: str
    strings: list[str]


def pl(number: int | float, singular: str, plural: str):
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
    return ' '.join(out)

def pickrand[T](*args: T) -> T:
    """Pick a random argument."""
    return random.choice(args)

def to_str(obj: object) -> str:
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
    
    def __init__(self, definitions_path: str = 'definitions.yaml', strings_path: str | None = None):
        """Initialize the localization system with paths to definition and string files."""
        self.definitions_path: str = definitions_path
        self.strings_path: str | None = strings_path
        
        # Initialize storage
        self.strings: list[Match] = []
        self.strings_dict: dict[str, Match] = {}
        self.simple_matches: dict[str, Match] = {}
        self.regex_matches: list[Match] = []
        self.translated_strings: dict[str, TranslatedString] = {}
        
        # Load definitions and translations
        self.load_definitions()
        self.load_translations()
    
    
    def load_definitions(self):
        """Load string definitions from YAML file."""
        yaml = YAML()
        with open(self.definitions_path, 'r', encoding="utf-8") as f:
            defs: dict[str, str | yamllib.CommentedSeq] = yaml.load(f)
            if not isinstance(defs, dict):
                defs = {}
            else:
                defs = cast(dict[str, str | yamllib.CommentedSeq], defs)  # satisfy pyright
            
        self.strings.clear()
        self.simple_matches.clear()
        self.regex_matches.clear()
        self.strings_dict.clear()
        
        # Load string definitions
        for key, match_str in defs.items():
            if isinstance(match_str, yamllib.CommentedSeq):
                for sub_match_str in match_str:
                    self.strings.append(Match(key, sub_match_str))
            else:
                self.strings.append(Match(key, match_str))
        
        # Index strings for faster lookup
        for matcher in self.strings:
            if matcher.key in self.simple_matches:
                if matcher.key != "-":
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
        if not os.path.exists(self.strings_path):
            logger.warning(f"Strings file not found: {self.strings_path}")
            return
            
        with open(self.strings_path, 'r', encoding='utf-8') as f:
            defs: dict[str, str | yamllib.CommentedSeq] = yaml.load(f)
            if not isinstance(defs, dict):
                defs = {}
            else:
                defs = cast(dict[str, str | yamllib.CommentedSeq], defs)  # satisfy pyright
            
        self.translated_strings.clear()
        
        for key, trans_str in defs.items():
            strs = []
            if isinstance(trans_str, yamllib.CommentedSeq):
                strs = [x for x in trans_str]
            else:
                strs = [trans_str]
            self.translated_strings[key] = TranslatedString(key, strs)
    
    def _fill_args(self, in_str: str, in_args: list[str | int | float], named_args: dict[str, str] | None = None) -> str:
        """Fill in arguments in a format string."""
        if not named_args:
            named_args = {}
        expl_args: dict[str, str] = {}
        results: list[str] = FSTRINGS.findall(in_str)
        for a in results:
            expl_args[a] = ''
        for argname, argvalue in zip(expl_args, in_args):
            expl_args[argname] = to_str(argvalue)
        expl_args.update(named_args)
        return in_str.format_map(expl_args)
    
    def identify_string(self, in_str: str):
        if in_str in self.simple_matches:
            return self.simple_matches[in_str]
        else:
            for m in self.regex_matches:
                if m.regex and m.regex.match(in_str):
                    return m
        return None

    def translate_string(self, in_str: str, in_args: list[str | int | float], match: 'Match | None' = None, additional_args: dict[str, str] | None = None) -> str:
        """
        Translate a string using the loaded definitions and translations.
        
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
    
    def s(self, in_str: str, **kwargs) -> str:
        """
        Shorthand method to get a translated string with named arguments.
        
        Args:
            in_str: The input string to translate
            **kwargs: Named arguments for formatting
            
        Returns:
            str: The translated string
        """
        return self.translate_string(in_str, [], additional_args=kwargs)
        
    
    def getstr(self, key: str, args: dict[str, str] | None = None) -> str:
        """
        Get a translated string by key with the given arguments.
        
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
            raise ValueError(f"String key not found: {key}")
            
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
            raise ValueError(f"No string found for key: {key}")
        
        # Use default string if no translation is available
        if not trans_str:
            trans_str = default_str
            
        return matcher.translate(trans_str, default_str, cast(dict[str, str | int | float], args))

class Match:
    def __init__(self, key: str, match_string: str = ""):
        self.key: str = key
        self.match_string: str = match_string
        self.arguments: list[StringArg] = []
        regex_str_build: list[str] = ['^']
        orig_str_build: list[str] = []
        has_regex = False
        for mo in MATCH_DEF_TOKENIZER.finditer(match_string):
            kind = mo.lastgroup
            value = mo.groupdict().get(kind or "", "")
            match kind:
                case "nothing":
                    regex_str_build.append(re.escape(value))
                    orig_str_build.append(value)
                case "parsed":
                    name = value
                    matcher = ".+"
                    split = value.split(":", 1)
                    if len(split) == 2:
                        name, matcher = split
                    self.arguments.append(StringArg(
                        name, StringArgType.PARSED
                    ))
                    regex_str_build.append(
                        f"({matcher})"
                    )
                    orig_str_build.append("{%s}" % name)
                    has_regex = True
                case "given":
                    self.arguments.append(
                        StringArg(value, StringArgType.GIVEN)
                    )
                    regex_str_build.append(re.escape(value))
                    orig_str_build.append("{%s}" % value)
                case _:
                    logger.error("Unexpected match group in string pattern")
                    assert False, "Unexpected match group in string pattern"
        self.regex: re.Pattern[str] | None = None
        if has_regex:
            regex_str_build.append("$")
            self.regex = re.compile("".join(regex_str_build))
            self.match_string = "".join(orig_str_build)
            # print(self.regex)
            # print(self.match_string)
    
    def extract_args(self, rf_string: str, rf_args: list[str | int | float]) -> dict[str, str | int | float]:
        # expl_args = [x for x in self.arguments if x.arg_type == StringArgType.GIVEN]
        expl_args: dict[str, None] = {}
        for a in FSTRINGS.findall(rf_string):
            # ordered set
            expl_args[a] = None
        impl_args = [x.name for x in self.arguments if x.arg_type == StringArgType.PARSED]
        mapped_args: dict[str, str | int | float] = {}
                
        if len(impl_args) > 0:
            if not self.regex:
                raise ValueError("Matcher has no regex pattern")
            groups: list[str] = self.regex.findall(rf_string)
            if len(groups) == 1:
                # if isinstance(groups[0], str):
                mapped_args[impl_args[0]] = groups[0]
                # else:
                #     for idx, g in enumerate(groups[0]):
                #         mapped_args[impl_args[idx]] = g
            else:
                raise ValueError("Input string may not match this matcher")
            
        for idx, argname in enumerate(expl_args):
            mapped_args[argname] = rf_args[idx]

        return mapped_args
            
    def translate(self, trans_string: str, rf_string: str, rf_args: list[str | int | float] | dict[str, str | int | float], additional_args: dict[str, str] = {}) -> str:
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
        eval_globals.update({
            "pl": pl,
            "llb": "{{",
            "rrb": "}}",
            "pick": pickrand,
            "unping": unping,
            "fill": fill
        })
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
                            string_build.append("{%s}" % value)
                    case "eval":
                        try:
                            logger.debug(f"Evaluating expression: {value}")
                            eval_out: Any = eval(value, eval_globals)
                        except Exception as e:
                            logger.error(f"Evaluation failed for expression '{value}': {e}")
                            eval_out = "(?)"
                        string_build.append(to_str(eval_out))
                    case _:
                        logger.warning(f"Unknown token type: {kind}")
            str_b = "".join(string_build)
                
            str_a, str_b = (str_b, str_a)

        # while str_a != str_b:
        #     str_b = str_a.format_map(mapped_args)
        #     str_a, str_b = (str_b, str_a)
        return str_b
    
    @override
    def __repr__(self):
        return f"Match({self.key})"

# def _test():
#     """Test function for the localization system."""
#     logger.basicConfig(
#         level=logger.DEBUG,
#         format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#         handlers=[
#             logger.StreamHandler(),
#         ]
#     )
#     # Example test cases
#     test_input = "You have {scrollCount} scrolls, for a total multiplier of 60x! Use those {scrollCount} scrolls wisely! when the {a} does {b} and {c}"
#     test_translate = "{scrollCount} scrolls? {multAmount} multiplier? Wowee! I love using {{pl(scrollCount, 'my scroll', 'all of my {scrollCount} scrolls', False)}}! {ffstringTest}"
#     test_args = [1, 1, 2, 3]
    
#     # Initialize the localization system
#     loc = RavenfallLocalization()
    
#     # Example usage
#     try:
#         # Example of using translate_string
#         result = loc.translate_string(
#             "{type0}: {playerName0}, {playerName1} ({playerStats0})",
#             ["Mining", "Player1", "Lvl 99"]
#         )
#         print("Translated string:", result)
        
#         # Example of using getstr with bot strings
#         bot_msg = loc.getstr(
#             BotString.RAVENFALL_CONNECTED,
#             {"prefix": "!"}
#         )
#         print("Bot message:", bot_msg)
        
#     except Exception as e:
#         logger.error(f"Error: {e}", exc_info=True)

# if __name__ == "__main__":
#     _test()