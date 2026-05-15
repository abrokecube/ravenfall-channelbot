from msgspec import json


class BaseRavenBotPayload:
    def __init__(self, identifier: str, content: object) -> None:
        self.identifier: str = identifier
        self.content: object = content

    def get_content_json_string(self) -> str:
        """Get the message content as a JSON string."""
        return json.encode(self.content).decode("utf-8")
