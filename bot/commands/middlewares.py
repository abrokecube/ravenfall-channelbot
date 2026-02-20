from .events import MessageEvent
from .global_context import GlobalContext

TEXT_REPLACEMENTS = {
    "\U000e0000": None,
    "\u034f": None,
}
TEXT_TRANS = str.maketrans(TEXT_REPLACEMENTS)
def filter_text(text: str):
    text = text.translate(TEXT_TRANS)
    text = text.strip()
    return text

def filter_message_event_text(global_ctx: GlobalContext, event: MessageEvent):
    event.text = filter_text(event.text)
