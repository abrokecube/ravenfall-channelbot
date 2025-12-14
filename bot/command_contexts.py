from __future__ import annotations

from typing import Callable, List, Dict, Awaitable, Union, Optional, TYPE_CHECKING, Protocol, runtime_checkable, Any, Type, TypeVar, cast, get_origin, get_args
from .command_enums import OutputMessageType, Platform, UserRole, BucketType, CustomRewardRedemptionStatus
from utils.strutils import split_by_utf16_bytes

if TYPE_CHECKING:
    from .commands import Commands, Command
    from twitchAPI.chat import ChatMessage, Twitch
    from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionData
    from .chat_system import Message as ServerChatMessage, ChatManager as ServerChatManager


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
    platform_character_limit: int
    data: Any
    
    async def reply(self, text: str) -> None:
        ...

    async def send(self, text: str) -> None:
        ...

    def get_bucket_key(self, bucket_type: BucketType) -> Any:
        ...

def filter_text(context: Context, text: str):
    if context.platform_output_type == OutputMessageType.SINGLE_LINE:
        text = " - ".join(text.splitlines())
    split_text = [text]
    if context.platform_character_limit is not None and context.platform_character_limit > 0:
        split_text = split_by_utf16_bytes(text, context.platform_character_limit)
    return split_text

class TwitchContext(Context):
    """Twitch-specific command context.
    
    Implements the Context protocol without inheriting from it.
    Common fields (prefix, invoked_with, command) should be populated
    using populate_common_fields() after construction.
    """
    
    def __init__(self, msg: 'ChatMessage', twitch: 'Twitch' = None):
        # Platform-specific fields
        self.message = msg.text
        self.author = msg.user.name
        self.platform = Platform.TWITCH
        self.platform_allows_markdown = False
        self.platform_output_type = OutputMessageType.SINGLE_LINE
        self.platform_character_limit = 500
        self.data: ChatMessage = msg
        self.api: Twitch = twitch
        self.roles = self._compute_roles()
        
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
        
        if self.data.user.mod or self.data.user.name == self.data.room.name:
            roles.append(UserRole.MODERATOR)
        
        if self.data.user.subscriber:
            roles.append(UserRole.SUBSCRIBER)
        
        owner_username = os.getenv("OWNER_TWITCH_USERNAME")
        if owner_username and self.data.user.name.lower() == owner_username.lower():
            roles.append(UserRole.BOT_OWNER)
            roles.append(UserRole.ADMIN)
        
        return roles
    
    async def reply(self, text: str) -> None:
        """Reply to the message that triggered the command."""
        for text_ in filter_text(self, text):
            await self.data.reply(text_)
    
    async def send(self, text: str) -> None:
        """Send a message to the channel."""
        for text_ in filter_text(self, text):
            await self.data.chat.send_message(self.data.room.name, text_)

    def get_bucket_key(self, bucket_type: BucketType) -> Any:
        if bucket_type == BucketType.USER:
            return self.data.user.id
        elif bucket_type == BucketType.CHANNEL:
            return self.data.room.room_id
        elif bucket_type == BucketType.GUILD:
            return self.data.room.room_id # Twitch doesn't have guilds, map to channel
        elif bucket_type == BucketType.DEFAULT or bucket_type == BucketType.GLOBAL:
            return None # Global bucket
        return None

class TwitchRedeemContext(Context):
    """Twitch-specific redeem context."""
    
    def __init__(self, redemption: 'ChannelPointsCustomRewardRedemptionData', bot: 'Commands'):
        self.redemption = redemption
        self.bot = bot
        self.platform = Platform.TWITCH
        self.platform_allows_markdown = False
        self.platform_output_type = OutputMessageType.SINGLE_LINE
        self.data = redemption
        self.message = redemption.user_input or ""
        self.author = redemption.user_login
        self.prefix = ""
        self.invoked_with = redemption.reward.title
        self.command = None
        self.parameters = self.message
        self.platform_character_limit = 500
        self.roles = [UserRole.USER] # Basic role assignment for now

    async def reply(self, text: str) -> None:
        """Reply to the user who redeemed."""
        # We can't reply directly to a redemption, so we send a message to chat
        for text_ in filter_text(self, text):
            await self.bot.chat.send_message(self.redemption.broadcaster_user_login, f"@{self.author} {text_}")

    async def send(self, text: str) -> None:
        """Send a message to the channel."""
        for text_ in filter_text(self, text):
            await self.bot.chat.send_message(self.redemption.broadcaster_user_login, text_)

    def get_bucket_key(self, bucket_type: BucketType) -> Any:
        if bucket_type == BucketType.USER:
            return self.redemption.user_id
        elif bucket_type == BucketType.CHANNEL:
            return self.redemption.broadcaster_user_id
        elif bucket_type == BucketType.GUILD:
            return self.redemption.broadcaster_user_id
        elif bucket_type == BucketType.DEFAULT or bucket_type == BucketType.GLOBAL:
            return None
        return None

    async def update_status(self, status: CustomRewardRedemptionStatus):
        if self.redemption.status == "unfulfilled":
            twitch = self.bot.twitch
            if self.redemption.broadcaster_user_id in self.bot.twitches:
                twitch = self.bot.twitches[self.redemption.broadcaster_user_id]
            await twitch.update_redemption_status(
                self.redemption.broadcaster_user_id,
                self.redemption.reward.id,
                self.redemption.id,
                status
            )
        else:
            # logger.warning(f"Redemption is not in the UNFULFILLED state (current: {self.redemption.status})")
            pass
    
    async def fulfill(self):
        await self.update_status(CustomRewardRedemptionStatus.FULFILLED)
    
    async def cancel(self):
        await self.update_status(CustomRewardRedemptionStatus.CANCELED)

class ServerContext(Context):
    """Context for commands triggered via the custom chat server."""
    
    def __init__(self, message: 'ServerChatMessage', chat_manager: 'ServerChatManager'):
        self.message = message.content
        self.author = message.author
        self.prefix = ""
        self.invoked_with = ""
        self.command = None
        self.parameters = ""
        self.platform = Platform.SERVER
        self.platform_allows_markdown = True
        self.platform_output_type = OutputMessageType.MULTI_LINE
        self.platform_character_limit = None
        self.data: ServerChatMessage = message
        self.bot = None
        self.chat_manager: ServerChatManager = chat_manager
        self.roles = self._compute_roles()
        
    def _compute_roles(self):
        roles = [UserRole.USER]
        if self.data.user_id == "admin":
            roles.extend([UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR])
        return roles
        
    async def reply(self, text: str) -> None:
        """Reply to the message."""
        # Send message back to the same room
        for text_ in filter_text(self, text):
            await self.chat_manager.send_message(self.data.room_name, "Bot", text_, self.data.id)

    async def send(self, text: str) -> None:
        """Send a message to the channel."""
        for text_ in filter_text(self, text):
            await self.chat_manager.send_message(self.data.room_name, "Bot", text_)

    def get_bucket_key(self, bucket_type: BucketType) -> Any:
        if bucket_type == BucketType.USER:
            return self.author
        elif bucket_type == BucketType.CHANNEL:
            return self.data.room_name
        elif bucket_type == BucketType.GUILD:
            return self.data.room_name
        return None
