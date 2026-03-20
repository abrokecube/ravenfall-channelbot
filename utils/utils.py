# pyright: reportPrivateUsage=false
from datetime import datetime
import string
from collections.abc import Collection, Callable
from typing import cast, NamedTuple

import thefuzz.process
import os
import thefuzz

import ravenpy

import re 
from enum import Enum
from math import inf
import aiohttp

def parse_time(iso_str: str):
    s = ""
    if iso_str[-1] == "Z":
        s = iso_str[:-1] + '+00:00'
    else:
        s = iso_str + '+00:00'
    return datetime.fromisoformat(s)

def unping(in_str: str) -> str:
    out: list[str] = []
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


class SplitWildcard:
    def __init__(self, min_words: int = 0):
        self.min_words: int = min_words

class SplitFuzzyRatio(Enum):
    SIMPLE_RATIO = 1
    PARTIAL_RATIO = 2
    TOKEN_SORT_RATIO = 3
    TOKEN_SET_RATIO = 4
    PARTIAL_TOKEN_SORT_RATIO = 5
    PARTIAL_TOKEN_SET_RATIO = 6
      
_split_fuzzy_funcs: dict[SplitFuzzyRatio, Callable[[str, str], int]] = {
    SplitFuzzyRatio.SIMPLE_RATIO: thefuzz.process.fuzz.ratio,  # pyright: ignore [reportUnknownMemberType]
    SplitFuzzyRatio.PARTIAL_RATIO: thefuzz.process.fuzz.partial_ratio,  # pyright: ignore [reportUnknownMemberType]
    SplitFuzzyRatio.TOKEN_SORT_RATIO: thefuzz.process.fuzz.token_sort_ratio,  # pyright: ignore [reportUnknownMemberType]
    SplitFuzzyRatio.TOKEN_SET_RATIO: thefuzz.process.fuzz.token_set_ratio,  # pyright: ignore [reportUnknownMemberType]
    SplitFuzzyRatio.PARTIAL_TOKEN_SORT_RATIO: thefuzz.process.fuzz.partial_token_sort_ratio,  # pyright: ignore [reportUnknownMemberType]
    SplitFuzzyRatio.PARTIAL_TOKEN_SET_RATIO: thefuzz.process.fuzz.partial_token_set_ratio,  # pyright: ignore [reportUnknownMemberType]
} 

class FuzzResult(NamedTuple):
    string: str
    score: int

class SplitQuery:
    def __init__(
            self, string_list: Collection[str], min_match_thresh: int=90, 
            match_word_count: bool=False, search_range: int=2, optional: bool=False,
            return_result_count: int = 5, match_count: int = 1,
            match_algo: SplitFuzzyRatio = SplitFuzzyRatio.SIMPLE_RATIO
            ):
        self.string_list: Collection[str] = string_list
        self.match_threshold: int = min_match_thresh
        self.match_word_count: bool = match_word_count
        self.search_range: int = search_range
        self.optional: bool = optional
        self.return_result_count: int = return_result_count
        self.max_match_count: int = match_count
        self.fuzzy_algo: SplitFuzzyRatio = match_algo
        self._grouped_by_word_count: dict[int, list[str]] = {}
        self._max_word_count: int = 0
        self._min_word_count: int = cast(int, inf)
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
        self._iterations: int = 0

class SplitResult:
    def __init__(self):
        self.text: str = ""
        self.match_score: int = 0
        self.match_results: Collection[tuple[str, int]] = tuple()
        self.match_query: str = ""

def split_arguments(in_str: str | tuple[str, ...], *queries: SplitQuery | SplitWildcard
) -> tuple[SplitResult, ...]:
    if isinstance(in_str, str):
        in_args = in_str.split()
    else:
        in_args = in_str
    ptr_start = 0
    ptr_length = 1
    advance_pointer = False
    prev_is_wildcard = False
    out_results: list[SplitResult] = [SplitResult() for _ in range(len(queries))]
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
            
        else:
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
                    query_result: list[FuzzResult] = cast(list[FuzzResult], thefuzz.process.extract(  # pyright: ignore [reportUnknownMemberType]
                        query_string, string_items, limit=query.return_result_count,
                        scorer=_split_fuzzy_funcs[query.fuzzy_algo]
                    ))
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
                # if out_results[idx].text is None:
                #     if query.optional:
                #         out_results[idx].text = ''
                #         ptr_length = 0
                #         # break
                #     if prev_is_wildcard:
                #         if not out_results[idx-1].text:
                #             out_results[idx-1].text = in_args[ptr_start]
                #         else:
                #             out_results[idx-1].text += f" {in_args[ptr_start]}"
                #         ptr_start += 1
                #     else:
                #         break
                # elif out_results[idx].text != '':
                if out_results[idx].text != '':
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
        # else:
        #     raise ValueError("Argument must be SplitQuery or SplitWildcard")
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


async def upload_to_bin(text: str):
    provider = os.getenv("PASTEBIN_PROVIDER", "pastes").lower()
    if provider == "pastes":
        return await upload_to_pastes(text)
    elif provider == "borkedbin":
        return await upload_to_borkedbin(text)
    else:
        raise ValueError(f"Invalid pastebin provider: {provider}")

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
        
async def upload_to_borkedbin(text: str) -> str | None:
    async with aiohttp.ClientSession() as s:
        r = await s.post(
            f"{os.getenv('BORKEDBIN_HOST', 'https://bin.borkedcube.moe').strip('/')}/api/add/text",
            headers={
                "X-Api-Key": os.getenv("BORKEDBIN_API_KEY", "")
            },
            json={"content": text}
        )
        if r.status == 200:
            result: dict[str, str] = cast(dict[str, str], await r.json())
            return result['url']
        else:
            _ = await r.text()  # Consume response to free connection
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

def fill_whitespace(text: str, pattern: str = ". "):
    """
    Replace whitespace runs with a repeated pattern, keeping a single real space
    at each edge of the run. The total length of the run is preserved.

    Example:
        "a          b" -> "a . . . .  b"
    """
    def repl(m: re.Match[str]) -> str:
        run = m.group(0)
        run_len = len(run)
        if run_len <= 2:
            # Too short to fit pattern inside — leave as-is
            return run

        # Keep 1 space at each end
        inner_len = run_len - 2
        repeated = (pattern * ((inner_len // len(pattern)) + 1))[:inner_len]

        return " " + repeated + " "

    return re.sub(r' +', repl, text)
