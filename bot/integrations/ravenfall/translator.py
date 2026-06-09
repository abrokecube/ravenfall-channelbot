from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ruamel.yaml import YAML

if TYPE_CHECKING:
    from re import Pattern

LOGGER = logging.getLogger(__name__)

TRANSLATE_TOKENIZER: Pattern[str] = re.compile(
    r"{(?P<given>[a-zA-Z_0-9]+)}|{{(?P<eval>((?:(?!}})).)+)}}|(?P<nothing>[^{}]*)"
)


def _pluralize(number: int, singular: str, plural: str) -> str:
    if number == 1:
        return singular
    return plural


def _unping(in_str: str) -> str:
    out: list[str] = []
    for word in in_str.split():
        if len(word) < 3:  # noqa: PLR2004
            out.append(word)
        elif len(word) < 6:  # noqa: PLR2004
            out.append(f"\U000e0000{word}")
        else:
            a = word[:-2]
            b = word[-2:]
            out.append(f"{a}\U000e0000{b}")
    return " ".join(out)


def _pickrand(*args: object) -> object:
    return random.choice(args)


def _to_str(obj: object) -> str:
    if isinstance(obj, float):
        if obj.is_integer():
            obj = int(obj)
        obj = f"{obj:,}"
    elif isinstance(obj, int):
        obj = f"{obj:,}"
    else:
        obj = str(obj)
    return obj


class TemplateTranslator:
    """Loads YAML translation files and evaluates {{expr}} templates.

    Each YAML key maps to a template string (or list of strings) that
    can contain {arg} placeholders (left for downstream format_message)
    and {{eval}} expressions (resolved immediately via eval loop).
    """

    def __init__(self) -> None:
        self._strings: dict[str, list[str]] = {}

    def load(self, path: str) -> None:
        """Load translation strings from a YAML file."""
        filepath = Path(path)
        if not filepath.exists():
            LOGGER.warning("Translation file not found: %s", path)
            return
        yaml = YAML()
        with filepath.open(encoding="utf-8") as f:
            data = yaml.load(f)
        if not isinstance(data, dict):
            return
        self._strings.clear()
        for key, value in data.items():
            if value is None:
                self._strings[key] = []
            elif isinstance(value, list):
                self._strings[key] = [str(x) if x is not None else "" for x in value]
            else:
                self._strings[key] = [str(value)]

    def translate(
        self, key: str, fmt: str, format_args: dict[str, object]
    ) -> str | None:
        """Translate a matched message using its identifier key.

        Args:
            key: The message identifier (from RavenfallFormattedMessage.identifier).
            fmt: The original format string (used by fill recursion).
            format_args: Extracted named arguments.

        Returns:
            The translated string with all {{}} evals resolved and {arg}
            placeholders left intact, or None if no translation is configured,
            or "" if the message should be suppressed.
        """
        strings = self._strings.get(key)
        if strings is None:
            return None
        if not strings:
            return ""

        template = random.choice(strings)
        return self._evaluate(template, fmt, format_args)

    def _evaluate(
        self, template: str, fmt: str, format_args: dict[str, object]
    ) -> str:
        def fill(key: str) -> str:
            result = self.translate(key, fmt, format_args)
            return result if result is not None else ""

        str_a = template
        str_b = ""
        eval_globals: dict[str, object] = {
            "pl": _pluralize,
            "llb": "{{",
            "rrb": "}}",
            "pick": _pickrand,
            "unping": _unping,
            "fill": fill,
            **dict(format_args),
        }
        while str_a != str_b:
            parts: list[str] = []
            for mo in TRANSLATE_TOKENIZER.finditer(str_a):
                kind = mo.lastgroup
                if kind is None:
                    continue
                value: str = mo.groupdict()[kind]
                match kind:
                    case "nothing":
                        parts.append(value)
                    case "given":
                        # Leave {arg} for downstream format_message()
                        parts.append(mo.group())
                    case "eval":
                        try:
                            eval_out: object = eval(  # noqa: S307
                                value, {"__builtins__": {}}, eval_globals
                            )
                        except Exception:
                            LOGGER.exception("Eval failed for '%s'", value)
                            eval_out = "(?)"
                        parts.append(_to_str(eval_out))
            str_b = "".join(parts)
            str_a, str_b = str_b, str_a
        return str_b
