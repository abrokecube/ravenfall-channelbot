from typing import Dict, Any, List, Optional, Union
from uuid import UUID
from .message_builders import (
    RavenBotMessageBuilder,
    RavenfallMessageBuilder,
    SenderBuilder,
    RecipientBuilder
)

class RavenBotTemplates:
    """Predefined templates for RavenBot messages."""
    
    @staticmethod
    def auto_raid_status(
        sender: Dict[str, Any],
        correlation_id: Optional[Union[str, UUID]] = None
    ) -> str:
        return RavenBotMessageBuilder(
            sender=sender,
            content="status",
            identifier="raid_auto",
            correlation_id=correlation_id
        ).build()

    @staticmethod
    def auto_join_raid(
        sender: Dict[str, Any],
        count: int = 2147483647,
        correlation_id: Optional[Union[str, UUID]] = None
    ) -> str:
        content = "on"
        if count != 2147483647:
            content = f"{count}"
        return RavenBotMessageBuilder(
            sender=sender,
            identifier="raid_auto",
            correlation_id=correlation_id,
            content=content
        ).build()
    

class RavenfallTemplates:
    """Predefined templates for Ravenfall messages."""
    
    @staticmethod
    def chat_message(
        message: str,
        correlation_id: Optional[Union[str, UUID]] = None,
        recipient: Dict[str, Any] = RecipientBuilder.system().build()
    ) -> str:
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
        ).build()
    


# Convenience instances
ravenbot = RavenBotTemplates()
ravenfall = RavenfallTemplates()
