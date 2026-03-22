from __future__ import annotations

from typing import TYPE_CHECKING, Callable
from collections.abc import Awaitable, Collection

if TYPE_CHECKING:
    from .components import BaseEventSource

from .enums import EventSource, UserRole
from .events import BaseEvent, MessageEvent, TwitchMessageEvent, TwitchRedemptionEvent
import os
import logging

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from twitchAPI.chat import Chat, ChatMessage, ChatUser
    from twitchAPI.object.eventsub import (
        ChannelPointsCustomRewardRedemptionData,
        ChannelPointsCustomRewardRedemptionAddEvent,
    )
    from twitchAPI.eventsub.websocket import EventSubWebsocket
    from twitchAPI.oauth import UserAuthenticator
    from twitchAPI import helper
    from bot.models import Channel
    from database.service import DatabaseService
from twitchAPI.twitch import Twitch, TwitchUser
from twitchAPI.type import (
    ChatEvent,
    AuthScope,
    MissingScopeException,
    InvalidTokenException,
)

TWITCH_APP_SCOPES = [AuthScope.USER_WRITE_CHAT]
TWITCH_BOT_USER_SCOPES = [
    AuthScope.CHAT_READ,
    AuthScope.CHAT_EDIT,
    AuthScope.USER_BOT,
    AuthScope.USER_WRITE_CHAT,
    AuthScope.MODERATOR_MANAGE_ANNOUNCEMENTS,
]
TWITCH_CHANNEL_SCOPES = [
    AuthScope.CHANNEL_MANAGE_REDEMPTIONS,
    AuthScope.CHANNEL_BOT,
]


class TwitchUtils:
    def __init__(self, twitch_event_src: "TwitchAPIEventSource"):
        self._src: "TwitchAPIEventSource" = twitch_event_src
        self.twitches: dict[str, Twitch] = twitch_event_src.channel_twitches


class TwitchAPIEventSource(BaseEventSource):
    def __init__(
        self,
        channels: list[Channel],
        bot_admin_uids: Collection[str],
        bot_user_id: str,
        twitch_app_id: str,
        twitch_app_secret: str,
    ):
        super().__init__()
        self.event_platform: EventSource = EventSource.Twitch
        self.channels: list[Channel] = channels
        self.bot_admin_uids: set[str] = set(bot_admin_uids)
        self.bot_user_id: str = bot_user_id
        self.twitch_app_id: str = twitch_app_id
        self.twitch_app_secret: str = twitch_app_secret

        self.chat: Chat | None = None
        self.bot_twitch: Twitch | None = None
        self.bot_user: TwitchUser | None = None
        self.channel_twitches: dict[str, Twitch] = {}
        self.eventsubs: list[EventSubWebsocket] = []

        self.db_service: DatabaseService | None = None

    async def get_twitch_auth_instance(
        self,
        user_id: int | str,
        user_name: str | None = None,
        scopes: list[AuthScope] = TWITCH_CHANNEL_SCOPES,
    ) -> Twitch:
        from twitchAPI.twitch import Twitch
        from twitchAPI.oauth import UserAuthenticator
        from twitchAPI.type import MissingScopeException, InvalidTokenException
        from twitchAPI import helper

        save_new_tokens = True
        access_token, refresh_token = await self.db_service.get_tokens(user_id)
        if access_token is not None:
            save_new_tokens = False

        while True:
            twitch = await Twitch(
                self.twitch_app_id,
                self.twitch_app_secret,
                target_app_auth_scope=TWITCH_APP_SCOPES,
            )
            if access_token is None:
                auth = UserAuthenticator(twitch, scopes, True)
                print(f"Auth scopes: {', '.join([x.value for x in scopes])}")
                print(
                    f"Please authenticate with the Twitch account: {user_name or user_id}"
                )
                result = await auth.authenticate(use_browser=False)
                if result is not None:
                    access_token, refresh_token = result
                else:
                    continue

            try:
                await twitch.set_user_authentication(
                    access_token, scopes, refresh_token
                )
                user: TwitchUser | None = None
                if save_new_tokens:
                    user = await helper.first(twitch.get_users())
                    if isinstance(user, TwitchUser):
                        await self.db_service.update_tokens(
                            user.id, access_token, refresh_token, user.login
                        )
            except MissingScopeException:
                print("Token is missing scopes")
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
            except InvalidTokenException:
                print("Invalid token")
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
            except Exception as e:
                print(f"Error setting user authentication: {e}")
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue

            if user is not None:
                if user.id == str(user_id):
                    return twitch
                else:
                    print("Token does not match user, please try again")
                    access_token = None
                    refresh_token = None
                    save_new_tokens = True
                    continue
            else:
                return twitch

    async def setup(self, event_manager: EventManager):
        from twitchAPI.chat import Chat
        from twitchAPI.type import ChatEvent
        from twitchAPI.eventsub.websocket import EventSubWebsocket
        from bot.core.services import DatabaseService
        from twitchAPI import helper

        self.db_service = event_manager.global_context.require_service(DatabaseService)

        LOGGER.info("Getting twitch info")
        self.bot_twitch = await self.get_twitch_auth_instance(
            self.bot_user_id, scopes=TWITCH_BOT_USER_SCOPES
        )

        LOGGER.info("Initializing twitch chat instance")
        self.chat = await Chat(
            self.bot_twitch, initial_channel=[x["channel_name"] for x in self.channels]
        )
        self.bot_user = await helper.first(self.bot_twitch.get_users())

        event_manager.global_context.register_service(Chat, self.chat)
        event_manager.global_context.register_service(Twitch, self.bot_twitch)
        event_manager.global_context.register_service(TwitchUtils, TwitchUtils(self))

        async def redemption_callback(redemption):
            await self.on_channel_point_redemption(redemption.event)

        async def _on_message(message: ChatMessage):
            await self.on_message(message)

        async def on_ready(ready_event):
            LOGGER.info("Twitch chat is ready")
            self.chat.register_event(ChatEvent.MESSAGE, _on_message)

        self.chat.register_event(ChatEvent.READY, on_ready)
        self.chat.start()

        LOGGER.info("Subscribing to twitch eventsub")
        for channel in self.channels:
            if channel.get("channel_points_redeems", False):
                channel_twitch = await self.get_twitch_auth_instance(
                    channel["channel_id"],
                    channel["channel_name"],
                    TWITCH_CHANNEL_SCOPES,
                )
                self.channel_twitches[channel["channel_id"]] = channel_twitch
                eventsub = EventSubWebsocket(channel_twitch)
                eventsub.start()
                try:
                    await eventsub.listen_channel_points_custom_reward_redemption_add(
                        channel["channel_id"],
                        redemption_callback,
                    )
                    LOGGER.info(f"Listening for redeems in {channel['channel_name']}")
                    self.eventsubs.append(eventsub)
                except Exception as e:
                    LOGGER.error(
                        f"Error listening for redeems in {channel['channel_name']}: {e}",
                        exc_info=True,
                    )
                    await eventsub.stop()

    async def teardown(self):
        if self.chat:
            self.chat.stop()
        for eventsub in self.eventsubs:
            await eventsub.stop()
        if self.bot_twitch:
            await self.bot_twitch.close()
        for twitch in self.channel_twitches.values():
            await twitch.close()

    def register_events(self, chat: Chat):
        chat.register_event(ChatEvent.MESSAGE, self.on_message)

    def _get_user_roles(self, user: ChatUser, room_id: str):
        roles = set()
        if user.id in self.bot_admin_uids:
            roles.add(UserRole.BOT_ADMINISTRATOR)
        if user.id == room_id:
            roles.add(UserRole.ADMINISTRATOR)
        if user.lead_mod:
            roles.add(UserRole.ADMINISTRATOR)
        if user.mod:
            roles.add(UserRole.MODERATOR)
        roles.add(UserRole.USER)
        return roles

    async def on_message(self, message: ChatMessage):
        await self.send_event(
            TwitchMessageEvent(
                data=message,
                text=message.text,
                id=message.id,
                author_login=message.user.name,
                author_name=message.user.display_name,
                author_id=message.user.id,
                author_roles=self._get_user_roles(message.user, message.room.room_id),
                room_name=message.room.name,
                room_id=message.room.room_id,
                bot_user_id=self.bot_user.id,
                bot_user_login=self.bot_user.login,
                bot_user_name=self.bot_user.display_name,
                bot_twitch=self.bot_twitch,
                channel_twitch=self.channel_twitches.get(message.room.room_id),
                twitch_chat=message.chat,
            )
        )

    async def on_channel_point_redemption(
        self, redemption: ChannelPointsCustomRewardRedemptionData
    ):
        await self.send_event(
            TwitchRedemptionEvent(
                data=redemption,
                text=redemption.user_input,
                id=redemption.id,
                author_login=redemption.user_login,
                author_name=redemption.user_name,
                author_id=redemption.user_id,
                author_roles=set([UserRole.USER]),
                room_name=redemption.broadcaster_user_login,
                room_id=redemption.broadcaster_user_id,
                bot_user_id=self.bot_user.id,
                bot_user_login=self.bot_user.login,
                bot_user_name=self.bot_user.display_name,
                bot_twitch=self.bot_twitch,
                channel_twitch=self.channel_twitches.get(
                    redemption.broadcaster_user_id
                ),
                twitch_chat=self.chat,
                redeem_name=redemption.reward.title,
                redeem_id=redemption.reward.id,
                redeem_cost=redemption.reward.cost,
            )
        )
