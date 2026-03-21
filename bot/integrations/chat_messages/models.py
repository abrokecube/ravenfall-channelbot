from typing import NamedTuple


class ChatRoomCapabilities(NamedTuple):
    multiline: bool
    max_message_length: int
