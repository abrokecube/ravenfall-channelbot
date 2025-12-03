from typing import Dict, List, Optional, Union, Any
from uuid import UUID, uuid4
import json

class SenderBuilder:
    """Helper class to build a Sender object for RavenBot messages."""
    
    def __init__(
        self,
        username: str,
        user_id: Union[str, UUID] = "00000000-0000-0000-0000-000000000000",
        character_id: Union[str, UUID] = "00000000-0000-0000-0000-000000000000",
        display_name: str = "",
        color: str = None,
        platform: str = "twitch",
        platform_id: str = None,
        is_broadcaster: bool = False,
        is_moderator: bool = False,
        is_subscriber: bool = False,
        is_vip: bool = False,
        is_game_administrator: bool = False,
        is_game_moderator: bool = False,
        sub_tier: int = 0,
        identifier: str = "1"
    ):
        """Initialize the SenderBuilder with user information."""
        self.sender_data = {
            "Id": str(user_id),
            "CharacterId": str(character_id),
            "Username": username,
            "DisplayName": display_name or username,
            "Color": color,
            "Platform": platform,
            "PlatformId": platform_id,
            "IsBroadcaster": is_broadcaster,
            "IsModerator": is_moderator,
            "IsSubscriber": is_subscriber,
            "IsVip": is_vip,
            "IsGameAdministrator": is_game_administrator,
            "IsGameModerator": is_game_moderator,
            "SubTier": sub_tier,
            "Identifier": identifier
        }
    
    def build(self) -> Dict[str, Any]:
        """Return the constructed sender dictionary."""
        return self.sender_data


class RecipientBuilder:
    """Helper class to build a Recipient object for Ravenfall messages."""
    
    def __init__(
        self,
        platform_id: str = '',
        platform_username: str = '',
        user_id: Union[str, UUID] = "00000000-0000-0000-0000-000000000000",
        character_id: Union[str, UUID] = "00000000-0000-0000-0000-000000000000",
        platform: str = "twitch",
    ):
        """Initialize the RecipientBuilder with recipient information."""
        self.recipient_data = {
            "UserId": str(user_id),
            "CharacterId": str(character_id),
            "Platform": platform,
            "PlatformId": platform_id,
            "PlatformUserName": platform_username
        }
    
    def build(self) -> Dict[str, Any]:
        """Return the constructed recipient dictionary."""
        return self.recipient_data

    @staticmethod
    def system():
        return RecipientBuilder(
            platform_id="",
            platform_username="",
            user_id="00000000-0000-0000-0000-000000000000",
            character_id="00000000-0000-0000-0000-000000000000",
            platform="system"
        )


class RavenBotMessageBuilder:
    """Helper class to build RavenBot messages."""
    
    def __init__(self, sender: Dict[str, Any], identifier: str, content: Any = {}, 
                 correlation_id: Optional[Union[str, UUID]] = None):
        """Initialize with required sender, content, and optional identifier and correlation ID.
        
        Args:
            sender: The sender information dictionary
            content: The message content
            identifier: Message identifier (default: "message")
            correlation_id: Optional correlation ID (will generate a new UUID if not provided)
        """
        if not sender:
            raise ValueError("Sender is required")
            
        self.message_data = {
            "Identifier": identifier,
            "CorrelationId": str(correlation_id) if correlation_id else str(uuid4()),
            "Sender": sender,
            "Content": json.dumps(content)
        }
    
    def with_sender(self, sender: Dict[str, Any]) -> 'RavenBotMessageBuilder':
        """Set the sender of the message."""
        self.message_data["Sender"] = sender
        return self
    
    def with_content(self, content: Any) -> 'RavenBotMessageBuilder':
        """Set the message content."""
        self.message_data["Content"] = json.dumps(content)
        return self
    
    def with_identifier(self, identifier: str) -> 'RavenBotMessageBuilder':
        """Set the message identifier."""
        self.message_data["Identifier"] = identifier
        return self
    
    def with_correlation_id(self, correlation_id: Union[str, UUID]) -> 'RavenBotMessageBuilder':
        """Set a specific correlation ID."""
        self.message_data["CorrelationId"] = str(correlation_id)
        return self
    
    def build(self) -> str:
        """Build and return the message dictionary."""
        return json.dumps(self.build_dict())

    def build_dict(self) -> Dict[str, Any]:
        """Build and return the message dictionary."""
        if "Content" not in self.message_data:
            raise ValueError("Message content is required")
        if "Sender" not in self.message_data:
            raise ValueError("Sender is required")
        return self.message_data


class RavenfallMessageBuilder:
    """Helper class to build Ravenfall messages."""
    
    def __init__(self, recipient: Dict[str, Any], format_str: str = "", args: Optional[List[str]] = None, 
                 identifier: str = "message", correlation_id: Optional[Union[str, UUID]] = None):
        """Initialize with required recipient and optional format, args, identifier, and correlation ID.
        
        Args:
            recipient: The recipient information dictionary
            format_str: Optional format string for the message
            args: Optional list of format arguments
            identifier: Message identifier (default: "message")
            correlation_id: Optional correlation ID (will generate a new UUID if not provided)
        """
        if not recipient:
            raise ValueError("Recipient is required")
            
        self.message_data = {
            "Identifier": identifier,
            "CorrelationId": str(correlation_id) if correlation_id else str(uuid4()),
            "Recipent": recipient,
            "Format": format_str or "",
            "Args": list(args) if args else [],
            "Tags": [],
            "Category": ""
        }
    
    def with_recipient(self, recipient: Dict[str, Any]) -> 'RavenfallMessageBuilder':
        """Set the recipient of the message."""
        self.message_data["Recipent"] = recipient
        return self
    
    def with_format(self, format_str: str) -> 'RavenfallMessageBuilder':
        """Set the message format string."""
        self.message_data["Format"] = format_str
        return self
    
    def with_args(self, *args: str) -> 'RavenfallMessageBuilder':
        """Set the format arguments."""
        self.message_data["Args"] = list(args)
        return self
    
    def add_arg(self, arg: str) -> 'RavenfallMessageBuilder':
        """Add a single format argument."""
        self.message_data["Args"].append(arg)
        return self
    
    def with_tags(self, *tags: str) -> 'RavenfallMessageBuilder':
        """Set the message tags."""
        self.message_data["Tags"] = list(tags)
        return self
    
    def add_tag(self, tag: str) -> 'RavenfallMessageBuilder':
        """Add a single tag."""
        if tag not in self.message_data["Tags"]:
            self.message_data["Tags"].append(tag)
        return self
    
    def with_category(self, category: str) -> 'RavenfallMessageBuilder':
        """Set the message category."""
        self.message_data["Category"] = category
        return self
    
    def with_identifier(self, identifier: str) -> 'RavenfallMessageBuilder':
        """Set the message identifier."""
        self.message_data["Identifier"] = identifier
        return self
    
    def with_correlation_id(self, correlation_id: Union[str, UUID]) -> 'RavenfallMessageBuilder':
        """Set a specific correlation ID."""
        self.message_data["CorrelationId"] = str(correlation_id)
        return self
    
    def build(self) -> str:
        """Build and return the message dictionary."""
        if "Recipent" not in self.message_data:
            raise ValueError("Recipient is required")
        if not self.message_data["Format"] and not self.message_data["Args"]:
            raise ValueError("Either Format or Args must be provided")
        return json.dumps(self.message_data)


class MessageFactory:
    """Factory class to create different types of messages."""
    
    @staticmethod
    def create_ravenbot_message(
        sender: Dict[str, Any],
        content: str,
        identifier: str = "message",
        correlation_id: Optional[Union[str, UUID]] = None
    ) -> str:
        """Create a RavenBot message.
        
        Args:
            sender: The sender information dictionary
            content: The message content
            identifier: Message identifier (default: "message")
            correlation_id: Optional correlation ID (will generate a new UUID if not provided)
            
        Returns:
            str containing the complete message
        """
        return RavenBotMessageBuilder(
            sender=sender,
            content=content,
            identifier=identifier,
            correlation_id=correlation_id
        ).build()
    
    @staticmethod
    def create_ravenfall_message(
        recipient: Dict[str, Any],
        format_str: str = "",
        args: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        category: str = "",
        identifier: str = "message",
        correlation_id: Optional[Union[str, UUID]] = None
    ) -> str:
        """Create a Ravenfall message.
        
        Args:
            recipient: The recipient information dictionary
            format_str: Format string for the message
            args: List of format arguments
            tags: Optional list of message tags
            category: Optional message category
            identifier: Message identifier (default: "message")
            correlation_id: Optional correlation ID (will generate a new UUID if not provided)
            
        Returns:
            str containing the complete message
        """
        builder = RavenfallMessageBuilder(
            recipient=recipient,
            format_str=format_str,
            args=args or [],
            identifier=identifier,
            correlation_id=correlation_id
        )
        
        if tags:
            builder.with_tags(*tags)
            
        if category:
            builder.with_category(category)
            
        return builder.build()

if __name__ == "__main__":
    MessageFactory.create_ravenbot_message(
        sender=SenderBuilder()
    )
