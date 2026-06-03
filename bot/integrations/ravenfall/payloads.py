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


class Sail(BaseRavenBotPayload):
    def __init__(self, destination: str | None = None) -> None:

        if destination:
            if not destination.lower() in {
                "home",
                "away",
                "ironhill",
                "kyo",
                "heim",
                "atria",
                "eldara",
            }:
                raise ValueError(f"Invalid island '{destination}'.")
            super().__init__("ferry_travel", destination)
        else:
            super().__init__("ferry_enter")


class Train(BaseRavenBotPayload):
    def __init__(self, skill: str):
        skill = skill.lower()
        if skill == "alchemy":
            skill = "brewing"

        identifier = ""
        content = {}
        match skill:
            case "sailing":
                identifier = "ferry_enter"
            case (
                "attack"
                | "defense"
                | "strength"
                | "all"
                | "magic"
                | "ranged"
                | "healing"
                | "health"
            ):
                identifier = "task"
                content = {"Task": "Fighting", "Arguments": [skill]}
            case (
                "woodcutting"
                | "fishing"
                | "mining"
                | "crafting"
                | "cooking"
                | "farming"
                | "gathering"
                | "brewing"
            ):
                identifier = "task"
                content: dict[str, str | list[str]] = {
                    "Task": skill.capitalize(),
                    "Arguments": [],
                }
            case _:
                raise ValueError("Invalid skill")
        super().__init__(identifier, content)
