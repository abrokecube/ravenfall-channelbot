from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.core.components import GlobalContext

    from .events import MessageEvent

TEXT_REPLACEMENTS = {
    "\U000e0000": None,
    "\u034f": None,
}
TEXT_TRANS = str.maketrans(TEXT_REPLACEMENTS)


def filter_text(text: str):
    """Filter text by applying replacements and stripping whitespace."""
    text = text.translate(TEXT_TRANS)
    text = text.strip()
    return text  # noqa: RET504


def filter_message_event_text(global_ctx: GlobalContext, event: MessageEvent):  # pyright: ignore[reportUnusedParameter]
    """Filter the text of a message event."""
    event.text = filter_text(event.text)
