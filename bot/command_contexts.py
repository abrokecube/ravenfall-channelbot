from __future__ import annotations

from typing import Callable, List, Dict, Awaitable, Union, Optional, TYPE_CHECKING, Protocol, runtime_checkable, Any, Type, TypeVar, cast, get_origin, get_args
from .command_enums import OutputMessageType, Platform, UserRole

if TYPE_CHECKING:
    from .commands import Commands, Command
    from twitchAPI.chat import ChatMessage

@runtime_checkable
class Context(Protocol):
    """Protocol defining the interface for all command contexts.
    
    This uses structural typing - classes don't need to inherit from this,
    they just need to have all these attributes and methods.
    """
    message: str
    full_message: str
    author: str
    roles: List[UserRole]
    prefix: str
    invoked_with: str
    parameters: str
    command: Command
    platform: Platform
    platform_allows_markdown: bool
    platform_output_type: OutputMessageType
    data: Any
    
    async def reply(self, text: str) -> None:
        ...

    async def send(self, text: str) -> None:
        ...


class TwitchContext(Context):
    """Twitch-specific command context.
    
    Implements the Context protocol without inheriting from it.
    Common fields (prefix, invoked_with, command) should be populated
    using populate_common_fields() after construction.
    """
    
    def __init__(self, msg: 'ChatMessage'):
        self.msg = msg
        
        # Platform-specific fields
        self.message = msg.text
        self.author = msg.user.name
        self.roles = self._compute_roles()
        self.platform = Platform.TWITCH
        self.platform_allows_markdown = False
        self.platform_output_type = OutputMessageType.SINGLE_LINE
        self.data: ChatMessage = msg
        
        self.prefix: str = ""
        self.invoked_with: str = ""
        self.command: Optional[Command] = None
        self.parameters: str = ""
    
    @property
    def full_message(self) -> str:
        return self.message

    def _compute_roles(self) -> List[UserRole]:
        """Compute user roles based on Twitch message."""
        import os
        
        roles = [UserRole.USER]
        
        if self.msg.user.mod or self.msg.user.name == self.msg.room.name:
            roles.append(UserRole.MODERATOR)
        
        if self.msg.user.subscriber:
            roles.append(UserRole.SUBSCRIBER)
        
        owner_username = os.getenv("OWNER_TWITCH_USERNAME")
        if owner_username and self.msg.user.name.lower() == owner_username.lower():
            roles.append(UserRole.BOT_OWNER)
            roles.append(UserRole.ADMIN)
        
        return roles
    
    async def reply(self, text: str) -> None:
        """Reply to the message that triggered the command."""
        await self.msg.reply(text)
    
    async def send(self, text: str) -> None:
        """Send a message to the channel."""
        await self.msg.chat.send_message(self.msg.room.name, text)

