from uuid import UUID
from .message_builders import (
    RavenBotMessageBuilder,
    RavenfallMessageBuilder,
    SenderBuilder,
    RecipientBuilder
)
from .models import Sender, Recipient

class RavenBotTemplates:
    """Predefined templates for RavenBot messages."""
    
    @staticmethod
    def auto_raid_status(
        sender: Sender,
        correlation_id: str | UUID | None = None
    ) -> RavenBotMessageBuilder:
        return RavenBotMessageBuilder(
            sender=sender,
            content="status",
            identifier="raid_auto",
            correlation_id=correlation_id
        )

    @staticmethod
    def auto_join_raid(
        sender: Sender,
        count: int = 2147483647,
        correlation_id: str | UUID | None = None
    ) -> RavenBotMessageBuilder:
        content = "on"
        if count != 2147483647:
            content = f"{count}"
        return RavenBotMessageBuilder(
            sender=sender,
            identifier="raid_auto",
            correlation_id=correlation_id,
            content=content
        )
    
    @staticmethod
    def sail(
        sender: Sender,
        correlation_id: str | UUID | None = None
    ) -> RavenBotMessageBuilder:
        return RavenBotMessageBuilder(
            sender=sender,
            identifier="ferry_enter",
            correlation_id=correlation_id
        )

    @staticmethod
    def sail_to(
        sender: Sender,
        island_name: str,
        correlation_id: str | UUID | None = None
    ) -> RavenBotMessageBuilder:
        return RavenBotMessageBuilder(
            sender=sender,
            identifier="ferry_travel",
            content=island_name,
            correlation_id=correlation_id
        )

    @staticmethod
    def gift_item(
        sender: Sender,
        recipient_user_name: str,
        item_name: str,
        item_count: int = 1,
        correlation_id: str | UUID | None = None,
    ) -> RavenBotMessageBuilder:
        a = RavenBotMessageBuilder(
            sender=sender,
            identifier="gift_item",
            correlation_id=correlation_id,
            content=f"{recipient_user_name} {item_name} {item_count}"
        )
        return a
    
    @staticmethod
    def query_item_count(
        sender: Sender,
        item_name: str,
        correlation_id: str | UUID | None = None,
    ) -> RavenBotMessageBuilder:
        a = RavenBotMessageBuilder(
            sender=sender,
            identifier="get_item_count",
            correlation_id=correlation_id,
            content=f"{item_name}"
        )
        return a
    
    @staticmethod
    def query_resources(
        sender: Sender,
        correlation_id: str | UUID | None = None,        
    ):
        a = RavenBotMessageBuilder(
            sender=sender,
            identifier="player_resources",
            correlation_id=correlation_id,
        )
        return a

    @staticmethod
    def inspect(
        username: str,
        correlation_id: str | UUID | None = None,
    ):
        sender = SenderBuilder(
            username=username.lower(),
            display_name=username.lower(),
        )
        a = RavenBotMessageBuilder(
            sender=sender.build(),
            identifier="inspect",
            correlation_id=correlation_id,
        )
        return a
    
    @staticmethod
    def train(
        sender: Sender,
        skill: str,
        correlation_id: str | UUID | None = None,
        
    ):
        skill = skill.lower()
        if skill == "alchemy":
            skill = "brewing"
        
        identifier = ""
        content = {}
        match skill:
            case "sailing":
                identifier = "ferry_enter"
            case "attack" | "defense" | "strength" | "all" | "magic" | "ranged" | "healing" | "health":
                identifier = "task"
                content = {
                    "Task": "Fighting",
                    "Arguments": [skill]
                }
            case "woodcutting" | "fishing" | "mining" | "crafting" | "cooking" | "farming" | "gathering" | "brewing":
                identifier = "task"
                content: dict[str, str | list[str]] = {
                    "Task": skill.capitalize(),
                    "Arguments": []
                }
            case _:
                raise ValueError("Invalid skill")

        a = RavenBotMessageBuilder(
            sender=sender,
            identifier=identifier,
            content=content,
            correlation_id=correlation_id
        )
        return a
    

SYSTEM = RecipientBuilder.system().build()
class RavenfallTemplates:
    """Predefined templates for Ravenfall messages."""
    
    @staticmethod
    def chat_message(
        message: str,
        correlation_id: str | UUID | None = None,
        recipient: Recipient = SYSTEM
    ):
        """Create a chat message for Ravenfall.
        
        Args:
            recipient: Recipient information dictionary
            sender_name: Name of the message sender
            message: The chat message
            channel: Channel name (default: "global")
            correlation_id: Optional correlation ID
            
        Returns:
            Formatted message string
        """
        return RavenfallMessageBuilder(
            recipient=recipient,
            format_str=message,
            args=[],
            identifier="message",
            correlation_id=correlation_id
        )
    


# Convenience instances
ravenbot = RavenBotTemplates()
ravenfall = RavenfallTemplates()
