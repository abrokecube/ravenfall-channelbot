from typing import Iterable, List, Dict, NamedTuple, Optional, Pattern
from enum import Enum
import re
import os
import logging
from ruamel.yaml import YAML
from ruamel import yaml as yamllib
from dataclasses import dataclass, field
import random

# Configure logging
logger = logging.getLogger(__name__)

# Compiled regex patterns for string matching and translation
MATCH_DEF_TOKENIZER: Pattern = re.compile(r"{(?P<given>[a-zA-Z_0-9]+)}|{{(?P<parsed>[a-zA-Z_0-9]+(:((?:(?!}})).)+)?)}}|(?P<nothing>[^{}]*)")
TRANSLATE_TOKENIZER: Pattern = re.compile(r'{(?P<given>[a-zA-Z_0-9]+)}|{{(?P<eval>((?:(?!}})).)+)}}|(?P<nothing>[^{}]*)')
RE_ESCAPE: Pattern = re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*")')
FSTRINGS: Pattern = re.compile(r'{([a-zA-Z_0-9]+)}')

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
    strings: List[str]


def pl(number: int | float, singular: str, plural: str):
    """Pluralization helper function."""
    if not isinstance(number, (int, float)):
        number = float(number)
    if number == 1:
        return singular
    return plural

def unping(in_str: str):
    """Prevent pings in messages by adding invisible characters."""
    out = []
    for word in in_str.split():
        if len(word) < 3:
            out.append(word)
        elif len(word) < 6:
            out.append(f"\U000e0000{word}")
        else:
            a = word[:-2]
            b = word[-2:]
            out.append(f"\U000e0000{a}\U000e0000{b}")
    return ' '.join(out)

def pickrand(*args):
    """Pick a random argument."""
    return random.choice(args)

class RavenfallLocalization:
    """Handles string localization and translation for Ravenfall bot."""
    
    def __init__(self, definitions_path: str = 'definitions.yaml', strings_path: str = None):
        """Initialize the localization system with paths to definition and string files."""
        self.definitions_path = definitions_path
        self.strings_path = strings_path
        
        # Initialize storage
        self.strings: List[Match] = []
        self.strings_dict: Dict[str, Match] = {}
        self.simple_matches: Dict[str, Match] = {}
        self.regex_matches: List[Match] = []
        self.translated_strings: Dict[str, TranslatedString] = {}
        
        # Load definitions and translations
        self.load_definitions()
        self.load_translations()
    
    
    def load_definitions(self):
        """Load string definitions from YAML file."""
        yaml = YAML()
        with open(self.definitions_path, 'r', encoding="utf-8") as f:
            defs = yaml.load(f)
            
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
        
        # Add default bot strings to the strings dict
        for key, value in _default_bot_strings.items():
            self.strings.append(Match(key, value))
        
        # Create a dictionary for quick lookup by key
        for a in self.strings:
            self.strings_dict[a.key] = a
    
    
    def load_translations(self):
        """Load string translations from YAML file."""
        yaml = YAML()
        if self.strings_path is None:
            logging.debug("No strings file was loaded")
            return
        if not os.path.exists(self.strings_path):
            logging.warning(f"Strings file not found: {self.strings_path}")
            return
            
        with open(self.strings_path, 'r', encoding='utf-8') as f:
            defs = yaml.load(f)
            
        self.translated_strings.clear()
        
        for key, trans_str in defs.items():
            strs = []
            if isinstance(trans_str, yamllib.CommentedSeq):
                strs = [x for x in trans_str]
            elif isinstance(trans_str, str):
                strs = [trans_str]
            self.translated_strings[key] = TranslatedString(key, strs)
    
    def _fill_args(self, in_str: str, in_args: List):
        """Fill in arguments in a format string."""
        expl_args = {}
        for a in FSTRINGS.findall(in_str):
            expl_args[a] = ''
        for argname, argvalue in zip(expl_args, in_args):
            expl_args[argname] = argvalue
        return in_str.format_map(expl_args)
    
    def get_key(self, in_str: str):
        if in_str in self.simple_matches:
            return self.simple_matches[in_str]
        else:
            for m in self.regex_matches:
                if m.regex.match(in_str):
                    return m
        return None

    def translate_string(self, in_str: str, in_args: List):
        """
        Translate a string using the loaded definitions and translations.
        
        Args:
            in_str: The input string to translate
            in_args: List of arguments to use for formatting
            
        Returns:
            str: The translated string
        """
        matched = self.get_key(in_str)
        if matched:
            logger.debug(f"Matched key {matched.key}")
        else:
            logger.warning(f"🚨🚨 No matched key for string: {in_str}")
            return f"{self._fill_args(in_str, in_args)}"
            
        translation = None
        if matched.key in self.translated_strings:
            translation = self.translated_strings[matched.key]
            
        if translation is None:
            logger.warning(f"No translation for {matched.key}")
            return self._fill_args(in_str, in_args)
            
        if not translation.strings:
            return ""
            
        translation_string = random.choice(translation.strings)
        return matched.translate(translation_string, in_str, in_args)
        
    
    def getstr(self, key: str, args: Dict[str, str] = None) -> str:
        """
        Get a translated string by key with the given arguments.
        
        Args:
            key: The key of the string to retrieve
            args: Dictionary of arguments to format the string with
            
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
        
        # Get default string if available
        if key in _default_bot_strings:
            default_str = _default_bot_strings[key]
        
        # Validate we have at least one string to work with
        if not default_str and not trans_str:
            raise ValueError(f"No string found for key: {key}")
        
        # Use default string if no translation is available
        if not trans_str:
            trans_str = default_str
            
        return matcher.translate(trans_str, default_str, args)

class Match:
    def __init__(self, key: str, match_string: str = ""):
        self.key = key
        self.match_string = match_string
        self.arguments: List[StringArg] = []
        regex_str_build = []
        orig_str_build = []
        has_regex = False
        for mo in MATCH_DEF_TOKENIZER.finditer(match_string):
            kind = mo.lastgroup
            value = mo.groupdict()[kind]
            match kind:
                case "nothing":
                    regex_str_build.append(RE_ESCAPE.sub(r'\\\1', value))
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
                    regex_str_build.append(RE_ESCAPE.sub(r'\\\1', "{%s}" % value))
                    orig_str_build.append("{%s}" % value)
                case _:
                    logger.error("Unexpected match group in string pattern")
                    assert False, "Unexpected match group in string pattern"
        self.regex = None
        if has_regex:
            self.regex = re.compile("".join(regex_str_build))
            self.match_string = "".join(orig_str_build)
            # print(self.regex)
            # print(self.match_string)
    
    def extract_args(self, rf_string: str, rf_args: List):
        # expl_args = [x for x in self.arguments if x.arg_type == StringArgType.GIVEN]
        expl_args = {}
        for a in FSTRINGS.findall(rf_string):
            # ordered set
            expl_args[a] = None
        impl_args = [x.name for x in self.arguments if x.arg_type == StringArgType.PARSED]
        mapped_args = {}
                
        if len(impl_args) > 0:
            groups = self.regex.findall(rf_string)
            if len(groups) == 1:
                if isinstance(groups[0], str):
                    mapped_args[impl_args[0]] = groups[0]
                else:
                    for idx, g in enumerate(groups[0]):
                        mapped_args[impl_args[idx]] = g
            else:
                raise ValueError("Input string may not match this matcher")
            
        for idx, argname in enumerate(expl_args):
            mapped_args[argname] = rf_args[idx]

        return mapped_args
            
    def translate(self, trans_string: str, rf_string: str, rf_args: List | Dict):
        if isinstance(rf_args, Dict):
            mapped_args = rf_args
        else:
            mapped_args = self.extract_args(rf_string, rf_args)
        def fill(string):
            return self.translate(string, rf_string, rf_args)
        str_a = trans_string
        str_b = ""
        eval_globals = {}
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
            string_build = []
            for mo in TRANSLATE_TOKENIZER.finditer(str_a):
                kind = mo.lastgroup
                value = mo.groupdict()[kind]
                match kind:
                    case "nothing":
                        string_build.append(value)
                    case "given":
                        if value in mapped_args:
                            string_build.append(str(mapped_args[value]))
                        else:
                            string_build.append("{%s}" % value)
                    case "eval":
                        try:
                            logger.debug(f"Evaluating expression: {value}")
                            eval_out = eval(value, eval_globals)
                        except Exception as e:
                            logger.error(f"Evaluation failed for expression '{value}': {e}")
                            eval_out = "(?)"
                        string_build.append(str(eval_out))
            str_b = "".join(string_build)
                
            str_a, str_b = (str_b, str_a)

        # while str_a != str_b:
        #     str_b = str_a.format_map(mapped_args)
        #     str_a, str_b = (str_b, str_a)
        return str_b


class BotString:
    """Container for bot string constants."""
    RAVENFALL_CONNECTED = "Connected to Ravenfall!"
    RAVENFALL_CONNECTED_WITHOUT_SESSION = "Connected to Ravenfall without session info... Please restart the bot or Ravenfall."
    RAVENFALL_NOT_CONNECTED = "Not connected to Ravenfall!"
    QUERY_PUBSUB = "yes there definitely is a pubsub here mhm"
    RELOADED_STRINGS = "Reloaded strings."
    ERROR = "bruh Error."
    TRAVEL_FAILED_INVALID_ISLAND = "You cannot travel to {query}. You can travel to Home, Away, Ironhill, Kyo, Heim, Atria, or Eldara."
    TRAVEL_ISLAND_MISSING = "Please specify an island to teleport to."
    NOT_PERMITTED = "NOPERS"
    NO_DUNGEONS_OR_RAIDS_AVAILABLE = "There are no active dungeons or raids at the moment."
    GAME_BUG_BROKE_THIS_COMMAND = "Game is bugged so this command is broken. aga"
    TOGGLE_UNSPECIFIED = "You need to specify what to toggle. (Options: helm or pet)"
    TOGGLE_FAILED = "{item} cannot be toggled."
    EXP_FAIL_NO_NUMBER = "Please enter a number."
    DUEL_FAIL_NO_ARGS = "To duel a player you need to specify their name. ex: '!duel JohnDoe', to accept or decline a duel request use '!duel accept' or '!duel decline'. You may also cancel an ongoing request by using '!duel cancel'"
    KICK_ARENA_FAIL_NO_ARGS = "Specify a player to kick."
    ADD_ARENA_FAIL_NO_ARGS = "Specify a player to kick."
    RAIDWAR_FAIL_NO_ARGS = "Enter a valid Twitch username."
    ITEM_NOT_IN_INVENTORY = "You do not have any {query}."
    ITEM_SEARCH_FAIL_NO_ITEM = "Please include an item name."
    ITEM_SEARCH_FAIL_NO_RESULT = "No results for '{query}'"
    ITEM_SEARCH_FAIL_NO_RESULT_SUGGEST = "Couldn't find '{query}', did you mean {suggestions}?"
    ITEM_FAIL_NO_VALID_ARGS = "uuh No valid item names were provided."
    ITEM_FAIL_NO_VALID_ARGS_SUGGEST = "uuh No valid item names were provided. (Did you mean {suggestions}?)"
    ITEMS = "You have {items}."
    EQUIP_FAIL_NO_ARGS = "You have to use {prefix}equip <item name> or {prefix}equip all for equipping your best items."
    UNEQUIP_FAIL_NO_ARGS = "You have to use {prefix}unequip <item name> or {prefix}unequip all for equipping your best items."
    GIFT_FAIL_NO_ARGS = "{prefix}gift <user> <item> (optional: amount)."
    TRAIN_FAIL_NO_ARGS = "You need to specify a skill to train, currently supported skills: all, atk, def, str, magic, ranged, fishing, cooking, woodcutting, mining, crafting, farming, healing, gathering, alchemy or !sail for sailing."
    TRAIN_FAIL_INVALID_ARG = "You cannot train '{query}'"
    TRAIN_SLAYER = "Join raids and dungeons to train Slayer!"
    ITEM_FAIL_MISSING_ITEM_NAME = "Please specify an item name."
    CRAFT_FAIL_ITEMS_NOT_LOADED = "Awkward Items have not been loaded yet. Wait a bit or ping abrokecube about it."
    CRAFT_FAIL_AMBIGUOUS_NUMBER = "uuh Please place an amount before the first item or after the last item to remove ambiguity. (I don't know if '{mysteryNumber}' is for the item before it or the item after it.)"
    CRAFT_FAIL_UNCRAFTABLE = "One item cannot be crafted."
    CRAFT_FAIL_MANY_NUMBER_UNCRAFTABLE = "{itemCount} items cannot be crafted."
    CRAFT_FAIL_MANY_UNCRAFTABLE = "{items} cannot be crafted."
    CRAFT_FAIL_CONTINUE_MANY = "Respond with yes to continue without these items."
    CRAFT_FAIL_CONTINUE = "Respond with yes to continue without this item."
    CRAFT_FAIL_MANY_ITEMS_MISSING_SKILLS_NUMBER = "{itemCount} items cannot be crafted because of missing skill requirements. You need {skills}."
    CRAFT_FAIL_MANY_MISSING_SKILLS = "{items} cannot be crafted because of missing skill requirements. You need {skills}."
    CRAFT_FAIL_ITEM = "Unable to craft {item}."
    CRAFT_FAIL_MANY_ITEMS = "Unable to craft items."
    CRAFT_FAIL_MISSING_INGREDIENTS_NUMBER = "You are missing {ingredientCount} ingredients!"
    CRAFT_FAIL_MISSING_INGREDIENTS = "You are missing {ingredients}!"
    CRAFT_FAIL_MISSING_INGREDIENTS_CRAFTABLE = "Respond with yes to craft these ingredients."
    CRAFT_PROCESSING_MANY_NUMBER = "Crafting {itemCount} items..."
    ENCHANT_REPLACE_FAIL_NO_RECENT_ITEMS = "You have not enchanted any items recently."
    ENCHANT_REPLACE_FAIL_TIMEOUT = "It has been more than 5 minutes since you enchanted '{item}', to avoid disenchanting by mistake you have to use '{prefix} disenchant last' to continue."
    COUNT_FAIL_NO_ARGS = "You must specify an item. Use {prefix}items (item name) or {prefix}count (item name)"

# Python black magic
_default_bot_strings: Dict[str, str] = {}
for key, value in BotString.__dict__.items():
    if "__" in key:
        continue
    new_key = "bot_" + key.lower()
    _default_bot_strings[new_key] = value
    setattr(BotString, key, new_key)

def _test():
    """Test function for the localization system."""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
        ]
    )
    # Example test cases
    test_input = "You have {scrollCount} scrolls, for a total multiplier of 60x! Use those {scrollCount} scrolls wisely! when the {a} does {b} and {c}"
    test_translate = "{scrollCount} scrolls? {multAmount} multiplier? Wowee! I love using {{pl(scrollCount, 'my scroll', 'all of my {scrollCount} scrolls', False)}}! {ffstringTest}"
    test_args = [1, 1, 2, 3]
    
    # Initialize the localization system
    loc = RavenfallLocalization()
    
    # Example usage
    try:
        # Example of using translate_string
        result = loc.translate_string(
            "{type0}: {playerName0}, {playerName1} ({playerStats0})",
            ["Mining", "Player1", "Lvl 99"]
        )
        print("Translated string:", result)
        
        # Example of using getstr with bot strings
        bot_msg = loc.getstr(
            BotString.RAVENFALL_CONNECTED,
            {"prefix": "!"}
        )
        print("Bot message:", bot_msg)
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)

if __name__ == "__main__":
    _test()