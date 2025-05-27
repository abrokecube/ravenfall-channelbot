from datetime import datetime, timedelta
import string
from typing import Iterable, Dict, List, Tuple

import thefuzz.process
import os
import thefuzz

import ravenpy

import re 
from enum import Enum
from math import inf
import aiohttp


class TimeSize(Enum):
    SMALL = 0
    SMALL_SPACES = 1
    MEDIUM = 2
    MEDIUM_SPACES = 3
    LONG = 4

def strjoin(connecting_char: str, *strings: str, before_end: str | None=None, include_conn_char_before_end=False):
    str_list = [str(x) for x in strings if x]
    if len(str_list) > 1 and before_end is not None:
        if include_conn_char_before_end:
            str_list[-1] = f"{before_end}{str_list[-1]}"
        else:
            a = str_list.pop()
            str_list[-1] += f"{before_end}{a}"
    
    return connecting_char.join(str_list)

def strjoin_list(connecting_char: str, *strings: str, before_end: str | None=None, include_conn_char_before_end=False):
    str_list = [str(x) for x in strings if x]
    if len(str_list) > 1 and before_end is not None:
        if include_conn_char_before_end:
            str_list[-1] = f"{before_end}{str_list[-1]}"
        else:
            a = str_list.pop()
            str_list[-1] += f"{before_end}{a}"
    out_list = []
    for string in str_list:
        out_list.append(string)
        out_list.append(connecting_char)
    out_list.pop()
    return out_list

def strenclose(open_char: str, close_char: str, connecting_char: str, *strings: str):
    out = [open_char + str(x) + close_char for x in strings if x]
    if len(out) == 0:
        return None
    return connecting_char.join(out)

def strjoin_len(connecting_char: str, max_chars: int, *strings: str):
    result, current = [], ""

    for s in filter(None, strings):
        if current:
            candidate = current + connecting_char + s
        else:
            candidate = s
        
        if len(candidate) > max_chars:
            result.append(current)
            current = s
        else:
            current = candidate
    
    if current:
        result.append(current)
    
    return result

def strextend(base, max_chars: int, *strings: str):
    if isinstance(base, str):
        result, current = [], base
    elif isinstance(base, list):
        result, current = base[:-1], base[-1] if base else ""
    else:
        raise TypeError("Base must be a string or a list of strings")
    
    for s in filter(None, strings):
        if current:
            candidate = current + s
        else:
            candidate = s
        
        if len(candidate) > max_chars:
            result.append(current)
            current = s
        else:
            current = candidate
    
    if current:
        result.append(current)
    
    return result
def strprefix(prefix: str, string: str):
    if string:
        return f"{prefix}{string}"
    else:
        return ''
def rm_words(string: str, num: int):
    split = string.split(' ')
    if num > 0:
        return " ".join(split[num:])
    elif num < 0:
        return " ".join(split[:num])
    else:
        return string

def parse_time(iso_str: str):
    s = ""
    if iso_str[-1] == "Z":
        s = iso_str[:-1] + '+00:00'
    else:
        s = iso_str + '+00:00'
    return datetime.fromisoformat(s)

def pl(number: int | float, word: str, include_number=True):
    if word[-1].lower() == 's':
        word = word[:-1]
    if include_number:
        if number == 1:
            return f"{number:,} {word}"
        else:
            return f"{number:,} {word}s"
    else:
        if number == 1:
            return f"{word}"
        else:
            return f"{word}s"
def unping(in_str: str):
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

def truncate_sentence(in_string: str, char_limit: int):
    if len(in_string) <= char_limit:
        return in_string
    
    excess_chars = len(in_string) - char_limit
    str_split = in_string.split(" ")
    while excess_chars > 0 and len(str_split) > 0:
        excess_chars -= len(str_split.pop()) + 1
    
    if len(str_split) == 0:
        str_split.append(in_string[:char_limit])

    return " ".join(str_split).strip(string.punctuation) + "…"

def format_timedelta(td: timedelta, size=TimeSize.SMALL_SPACES, max_terms=99) -> str:  
    return format_seconds(int(td.total_seconds()), size, max_terms)

_time_str = {
    'day': ('d', 'd', 'day', ' day', ' day'),
    'days': ('d', 'd', 'days', ' days', ' days'),
    'hour': ('h', 'h', 'hr', ' hr', ' hour'),
    'hours': ('h', 'h', 'hrs', ' hrs', ' hours'),
    'minute': ('m', 'm', 'min', ' min', ' minute'),
    'minutes': ('m', 'm', 'mins', ' mins', ' minutes'),
    'second': ('s', 's', 'sec', ' sec', ' second'),
    'seconds': ('s', 's', 'secs', ' secs', ' seconds'),
}


def format_seconds(seconds: int, size=TimeSize.SMALL, max_terms=99, include_zero=True):
    seconds = int(seconds)
    total_seconds = seconds
    negative = False
    if seconds < 0:
        seconds = -seconds
        negative = True
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    
    parts = []
    if days:
        word = _time_str['day'][size.value] if days == 1 else _time_str['days'][size.value]
        parts.append(f"{days}{word}")
    if hours or (include_zero and days) :
        word = _time_str['hour'][size.value] if hours == 1 else _time_str['hours'][size.value]
        parts.append(f"{hours}{word}")
    if minutes or (include_zero and any((hours, days))) :
        word = _time_str['minute'][size.value] if minutes == 1 else _time_str['minutes'][size.value]
        parts.append(f"{minutes}{word}")
    if seconds or (include_zero and any((minutes, hours, days))) or not parts:
        word = _time_str['second'][size.value] if seconds == 1 else _time_str['seconds'][size.value]
        parts.append(f"{seconds}{word}")
    if size == TimeSize.LONG and len(parts) > 1:
        # parts[-1] = "and " + parts[-1]
        # bye oxford comma
        last = parts.pop()
        parts[-1] += f" and {last}" 
    parts = parts[:max_terms]
    
    if negative:
        parts[0] = f"-{parts[0]}"
    
    if size == TimeSize.LONG:
        return ", ".join(parts).strip()
    elif size in [TimeSize.MEDIUM, TimeSize.MEDIUM_SPACES, TimeSize.SMALL_SPACES]:
        return " ".join(parts).strip()
    else:
        return "".join(parts).strip()


class SplitWildcard:
    def __init__(self, min_words=0):
        self.min_words = min_words

class SplitFuzzyRatio(Enum):
    SIMPLE_RATIO = 1
    PARTIAL_RATIO = 2
    TOKEN_SORT_RATIO = 3
    TOKEN_SET_RATIO = 4
    PARTIAL_TOKEN_SORT_RATIO = 5
    PARTIAL_TOKEN_SET_RATIO = 6
    
_split_fuzzy_funcs = {
    SplitFuzzyRatio.SIMPLE_RATIO: thefuzz.process.fuzz.ratio,
    SplitFuzzyRatio.PARTIAL_RATIO: thefuzz.process.fuzz.partial_ratio,
    SplitFuzzyRatio.TOKEN_SORT_RATIO: thefuzz.process.fuzz.token_sort_ratio,
    SplitFuzzyRatio.TOKEN_SET_RATIO: thefuzz.process.fuzz.token_set_ratio,
    SplitFuzzyRatio.PARTIAL_TOKEN_SORT_RATIO: thefuzz.process.fuzz.partial_token_sort_ratio,
    SplitFuzzyRatio.PARTIAL_TOKEN_SET_RATIO: thefuzz.process.fuzz.partial_token_set_ratio,
}
    
class SplitQuery:
    def __init__(
            self, string_list: Iterable[str], min_match_thresh=90, 
            match_word_count=False, search_range=2, optional=False,
            return_result_count = 5, match_count = 1,
            match_algo: SplitFuzzyRatio = SplitFuzzyRatio.SIMPLE_RATIO
            ):
        self.string_list = string_list
        self.match_threshold = min_match_thresh
        self.match_word_count = match_word_count
        self.search_range = search_range
        self.optional = optional
        self.return_result_count = return_result_count
        self.max_match_count = match_count
        self.fuzzy_algo = match_algo
        self._grouped_by_word_count: Dict[int, List[str]] = {}
        self._max_word_count = 0
        self._min_word_count = inf
        for string in self.string_list:
            words = len(string.split())
            if not words in self._grouped_by_word_count:
                self._grouped_by_word_count[words] = []
            self._grouped_by_word_count[words].append(string)
            if words > self._max_word_count:
                self._max_word_count = words
            if words < self._min_word_count:
                self._min_word_count = words
        if self._min_word_count == 0:
            self.optional = True
        self._iterations = 0

class SplitResult:
    def __init__(self):
        self.text: str | None = None
        self.match_score: int = 0
        self.match_results: Iterable[Tuple[str, int]] = tuple()
        self.match_query = ""

def split_arguments(in_str: str | Iterable[str], *queries: SplitQuery | SplitWildcard
) -> Tuple[SplitResult | None]:
    if isinstance(in_str, str):
        in_args = in_str.split()
    else:
        in_args = in_str
    ptr_start = 0
    ptr_length = 1
    advance_pointer = False
    prev_is_wildcard = False
    out_results = [SplitResult() for _ in range(len(queries))]
    # print(f"split_arguments with {len(in_args)} queries")
    
    idx = -1    
    for query in queries:
        idx += 1
        advance_pointer = False
        if isinstance(query, SplitWildcard):
            prev_is_wildcard = True
            ptr_length = query.min_words
            advance_pointer = True
            if ptr_start+ptr_length > len(in_args):
                break
            out_results[idx].text = " ".join(in_args[ptr_start:ptr_start+ptr_length])
            
        elif isinstance(query, SplitQuery):
            query._iterations = 1
            if query.max_match_count <= 0:
                continue
            
            if len(query.string_list) == 0 and query.optional:
                out_results[idx].text = ''
                continue

            if ptr_start >= len(in_args):
                if query.optional:
                    out_results[idx].text = ''
                    ptr_length = 0
                else:
                    break
            while ptr_start < len(in_args):
                for x in range(query._max_word_count+query.search_range,0,-1):
                    if query.match_word_count and not x in query._grouped_by_word_count:
                        continue
                    ptr_length = x
                    if ptr_start+ptr_length > len(in_args):
                        continue
                    if query.match_word_count:
                        string_items = query._grouped_by_word_count[x]
                    else:
                        string_items = query.string_list
                    query_string = " ".join(in_args[ptr_start:ptr_start+ptr_length])
                    query_result = thefuzz.process.extract(
                        query_string, string_items, limit=query.return_result_count,
                        scorer=_split_fuzzy_funcs[query.fuzzy_algo]
                    )
                    # print(f"{idx}: {query_string}")
                    result, score = query_result[0]
                    if score > out_results[idx].match_score:
                        out_results[idx].match_score = score
                        out_results[idx].match_results = query_result
                        out_results[idx].match_query = query_string
                        # print(f"   Matched {result} ({score})")
                        if score > query.match_threshold:
                            advance_pointer = True
                            out_results[idx].text = result
                            if score == 100:
                                break
                if out_results[idx].text is None:
                    if query.optional:
                        out_results[idx].text = ''
                        ptr_length = 0
                        # break
                    if prev_is_wildcard:
                        if not out_results[idx-1].text:
                            out_results[idx-1].text = in_args[ptr_start]
                        else:
                            out_results[idx-1].text += f" {in_args[ptr_start]}"
                        ptr_start += 1
                    else:
                        break
                elif out_results[idx].text != '':
                    if query._iterations >= query.max_match_count:
                        break
                    else:
                        query._iterations += 1
                        out_results.append(SplitResult())
                        prev_is_wildcard = False
                        idx += 1
                        if advance_pointer:
                            ptr_start += ptr_length
                else:
                    break
            prev_is_wildcard = False
        else:
            raise ValueError("Argument must be SplitQuery or SplitWildcard")
        if advance_pointer:
            ptr_start += ptr_length
        ptr_length = 1
    if prev_is_wildcard:
        if ptr_start < len(in_args):
            out_results[-1].text = ' '.join(in_args[ptr_start:])
    return tuple(out_results)

tw_username_re = re.compile(r"^@?[a-zA-Z0-9][\w]{2,24}$")
def is_twitch_username(text: str):
    return bool(tw_username_re.match(text))


async def upload_to_pastes(text: str):
    async with aiohttp.ClientSession() as s:
        r = await s.post(
            "https://api.pastes.dev/post",
            headers={
                "Content-Type": "text/plain"
            },
            data=text
        )
        if r.status == 201:
            return f"https://pastes.dev/{(await r.json())['key']}"
        else:
            return None

def get_char_identifier(char: ravenpy.Character):
    char_name = truncate_sentence(char.name, 40)
    if char_name == str(char.index):
        char_name = f"Character {char_name}"
    out_str = f"{char_name} ({char.character_index}, Lv{char.combat_level})"
    return out_str

def capitalize_first_letter(s: str):
    if not s:
        return s
    return s[0].upper() + s[1:]
