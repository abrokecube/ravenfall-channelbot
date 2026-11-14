"""Sends the game messages RavenBot drops because of its chat limit.

The towns' RavenBot (LocalBot) sends through TwitchLib with its default limit:
20 messages in a 30 second window that starts with the first one, and anything
over that is thrown away, not queued. Its own replies (help, cooldowns) count
too, and the middleman never sees those, so the bot can't predict the drops.

Instead it watches: each game message on its way to RavenBot is rendered the way
RavenBot would render it and kept as pending, each chat message from RavenBot's
account clears the pending entry it matches, and whatever is still pending after
`DELAY` seconds is sent by the bot's own account, threaded or mentioned the same
way RavenBot would have.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

DELAY = 1.5  # seconds RavenBot gets to post a message before the bot does
MAX_AGE = 60.0  # a message this old is let go rather than sent late
MAX_PENDING = 200
DUPLICATE_WINDOW = 30.0  # how long a resent message is remembered, to log RavenBot posting it after all
TWITCH_MAX_LENGTH = 499  # RavenBot's MessageUtilities.TwitchMaxMessageLength
FUZZY_MIN_LENGTH = 24
FUZZY_PREFIX = 40

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_LEADING_MENTION = re.compile(r"^@\S+?,?\s+")
_INVISIBLE = re.compile("[​-‏⁠﻿\U000e0000-\U000e007f]")
_IDENTIFIER_CHARS = set("qwertyuiopåasdfghjklöäzxcvbnm1234567890")


@dataclass
class PendingChat:
    text: str
    key: str
    reply_id: Optional[str]
    queued: float


def _fix_malformed(template: str) -> str:
    while True:
        previous = template
        template = (template.replace("{{", "{").replace("}}", "}").replace("  ", " ")
                    .replace("{ ", "{").replace(" }", "}").replace("{}", ""))
        if template == previous:
            return template


def _parameters(template: str) -> list[str]:
    """RavenBot's StringTemplateParser.AddParameters: each `{name` in order, repeats included."""
    names = []
    i = 0
    while i < len(template):
        if template[i] != "{":
            i += 1
            continue
        i += 1
        while i < len(template) and template[i].isspace():
            i += 1
        name = ""
        while i < len(template) and template[i].lower() in _IDENTIFIER_CHARS:
            name += template[i]
            i += 1
        if name:
            names.append(name)
    return names


def _arg_str(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def strip_meta(fmt: str) -> str:
    """RavenBot takes a leading %[category:...]% and %[tags:...]% off the format."""
    for start in ("%[category:", "%[tags:"):
        if fmt.startswith(start):
            end = fmt.find("]%")
            if end >= 0:
                fmt = fmt[end + 2:]
    return fmt


def render(fmt: str, args: Iterable[Any] | None) -> str:
    """The text RavenBot's ChatMessageFormatter makes from a game message."""
    fmt = strip_meta(fmt or "")
    if "{" not in fmt:
        return fmt
    args = list(args or [])
    output = _fix_malformed(fmt)
    for i, name in enumerate(_parameters(fmt)):
        if i >= len(args) or args[i] is None:
            continue
        value = _arg_str(args[i])
        if ("{" + name + " ") in output:
            output = output.replace("{" + name, value)
        else:
            output = output.replace("{" + name + "}", value)
    return output.replace("{", "").replace("}", "")


def split_message(message: str, max_length: int = TWITCH_MAX_LENGTH) -> list[str]:
    """RavenBot's MessageUtilities.SplitMessage: parts of at most max_length, cut at the last space."""
    parts = []
    index = 0
    while index < len(message):
        length = min(max_length, len(message) - index)
        split = length
        if length < len(message) - index:
            last_space = message.rfind(" ", index, index + length)
            if last_space >= index:
                split = last_space - index + 1
        parts.append(message[index:index + split].rstrip())
        index += split
    return [p for p in parts if p]


def normalize(text: str) -> str:
    text = _INVISIBLE.sub("", text or "")
    text = " ".join(text.split()).casefold()
    return _LEADING_MENTION.sub("", text, count=1)


def similar(a: str, b: str) -> bool:
    """Equal, or long enough and the same at the start, which covers an argument formatted differently."""
    if a == b:
        return True
    if min(len(a), len(b)) < FUZZY_MIN_LENGTH:
        return False
    n = min(FUZZY_PREFIX, len(a), len(b))
    return a[:n] == b[:n]


class RavenBotChatFallback:
    def __init__(
        self,
        send: Callable[[str, Optional[str]], Awaitable[Any]],
        command_prefixes: Iterable[str] = ("!",),
        clock: Callable[[], float] = time.monotonic,
        delay: float = DELAY,
        max_age: float = MAX_AGE,
        name: str = "",
    ):
        self.send = send
        self.command_prefixes = tuple(command_prefixes)
        self.clock = clock
        self.delay = delay
        self.max_age = max_age
        self.name = name
        self.pending: deque[PendingChat] = deque()
        self.resent: deque[PendingChat] = deque()
        self.resent_count = 0
        self.expired_count = 0
        self.duplicate_count = 0

    def expect(self, message: Mapping[str, Any]) -> None:
        """A game message on its way to RavenBot."""
        if str(message.get("Identifier", "")).lower() != "message":
            return
        recipient = message.get("Recipent") or {}
        platform = str(recipient.get("Platform") or "").lower()
        if platform not in ("twitch", "system"):
            return
        fmt = message.get("Format")
        if not fmt or not str(fmt).strip():
            return
        text = render(str(fmt), message.get("Args")).strip()
        if not text:
            return

        reply_id = None
        if platform == "twitch":
            cid = message.get("CorrelationId") or ""
            mention = recipient.get("PlatformUserName") or ""
            if cid:
                # RavenBot threads to it. Twitch message ids are UUIDs; anything else can't be threaded by the bot either
                if _UUID.match(cid):
                    reply_id = cid
                elif mention:
                    text = f"@{mention.lstrip('@')}, {text}"
            elif mention:
                text = f"@{mention.lstrip('@')}, {text}"

        now = self.clock()
        for part in split_message(text):
            self.pending.append(PendingChat(part, normalize(part), reply_id, now))
        while len(self.pending) > MAX_PENDING:
            self.pending.popleft()
            self.expired_count += 1

    def seen(self, text: str) -> bool:
        """A chat message from RavenBot's account. True if it was one the bot was waiting for."""
        key = normalize(text)
        if not key:
            return False
        for queue, is_resent in ((self.pending, False), (self.resent, True)):
            match = next((p for p in queue if p.key == key), None) or next((p for p in queue if similar(p.key, key)), None)
            if match is None:
                continue
            queue.remove(match)
            if is_resent:
                self.duplicate_count += 1
                logger.info(f"[{self.name}] RavenBot posted a message after the bot already resent it: {match.text!r}")
            return True
        return False

    def due(self) -> list[PendingChat]:
        """Takes the messages RavenBot hasn't posted in time, and lets go of the ones too old to send."""
        now = self.clock()
        while self.resent and now - self.resent[0].queued > DUPLICATE_WINDOW:
            self.resent.popleft()
        out = []
        while self.pending and now - self.pending[0].queued >= self.delay:
            entry = self.pending.popleft()
            if now - entry.queued > self.max_age:
                self.expired_count += 1
                continue
            out.append(entry)
        return out

    async def flush(self) -> int:
        """Sends what RavenBot dropped. Returns how many were sent."""
        sent = 0
        for entry in self.due():
            if entry.reply_id is None and entry.text.startswith(self.command_prefixes):
                # RavenBot would read it as a command from the bot's account
                logger.warning(f"[{self.name}] Not resending a dropped message that looks like a command: {entry.text!r}")
                continue
            entry.queued = self.clock()
            self.resent.append(entry)
            try:
                await self.send(entry.text, entry.reply_id)
            except Exception:
                logger.warning(f"[{self.name}] Failed to resend a message RavenBot dropped", exc_info=True)
                continue
            sent += 1
            self.resent_count += 1
            logger.info(f"[{self.name}] Resent a message RavenBot dropped: {entry.text!r}")
        return sent
