from __future__ import annotations

from typing import Callable, List, Dict, Awaitable, Union, Optional, TYPE_CHECKING, Protocol, runtime_checkable, Any, Type, TypeVar, cast, get_origin, get_args
from .command_enums import OutputMessageType, Platform, UserRole, BucketType, CustomRewardRedemptionStatus

if TYPE_CHECKING:
    from .commands import Commands, Command
    from twitchAPI.chat import ChatMessage, Twitch
    from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionData

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

    def get_bucket_key(self, bucket_type: BucketType) -> Any:
        ...


class TwitchContext(Context):
    """Twitch-specific command context.
    
    Implements the Context protocol without inheriting from it.
    Common fields (prefix, invoked_with, command) should be populated
    using populate_common_fields() after construction.
    """
    
    def __init__(self, msg: 'ChatMessage', twitch: 'Twitch'):
        # Platform-specific fields
        self.message = msg.text
        self.author = msg.user.name
        self.platform = Platform.TWITCH
        self.platform_allows_markdown = False
        self.platform_output_type = OutputMessageType.SINGLE_LINE
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
        await self.data.reply(text)
    
    async def send(self, text: str) -> None:
        """Send a message to the channel."""
        await self.data.chat.send_message(self.data.room.name, text)

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
        self.full_message = self.message
        self.author = redemption.user_login
        self.prefix = ""
        self.invoked_with = redemption.reward.title
        self.command = None
        self.parameters = self.message
        self.roles = [UserRole.USER] # Basic role assignment for now

    async def reply(self, text: str) -> None:
        """Reply to the user who redeemed."""
        # We can't reply directly to a redemption, so we send a message to chat
        await self.bot.chat.send_message(self.redemption.broadcaster_user_login, f"@{self.author} {text}")

    async def send(self, text: str) -> None:
        """Send a message to the channel."""
        await self.bot.chat.send_message(self.redemption.broadcaster_user_login, text)

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
            await self.bot.twitch.update_redemption_status(
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
