from msgspec import json


class BaseRavenBotPayload:
    def __init__(self, identifier: str, content: object = None) -> None:
        self.identifier: str = identifier
        if content is None:
            content = {}
        self.content: object = content

    def get_content_json_string(self) -> str:
        """Get the message content as a JSON string."""
        return json.encode(self.content).decode("utf-8")


class GetCoinCount(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("player_resources")


class GiftItem(BaseRavenBotPayload):
    def __init__(
        self, recipient_user_name: str, item_name: str, item_count: int = 1
    ) -> None:
        super().__init__("gift_item", f"{recipient_user_name} {item_name} {item_count}")


class GetItemCount(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("get_item_count", item_name)
