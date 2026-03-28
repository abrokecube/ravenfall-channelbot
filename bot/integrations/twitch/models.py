from typing import NamedTuple, TypedDict, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from . import TwitchChannelSettings
from .enums import EventSubTopic, MessageDeliveryMode, MessageRateMode, MessageReceiveMode
from dataclasses import dataclass


class EventSubChannelTopic(NamedTuple):
    """EventSub channel and topic."""

    channel_id: str
    topic: EventSubTopic


class EventSubCondition(TypedDict):
    """EventSub subscription condition."""

    broadcaster_user_id: str


class EventSubTransport(TypedDict):
    """EventSub subscription transport."""

    method: str
    session_id: str


class EventSubSubscriptionDict(TypedDict):
    """EventSub subscription object."""

    id: str
    status: str
    type: str
    version: str
    cost: int
    condition: EventSubCondition
    transport: EventSubTransport
    created_at: str


class EventSubRevocationDict(TypedDict):
    """EventSub revocation object."""

    subscription: EventSubSubscriptionDict


class ConnectedChat:
    """Represent a EventSub/IRC Twitch chat connection."""

    def __init__(self, channel_settings: TwitchChannelSettings) -> None:
        self.channel_id: str = channel_settings.id
        self.message_receive_mode: MessageReceiveMode = cast(
            MessageReceiveMode, channel_settings.message_receive_mode
        )
        self.message_delivery_mode: MessageDeliveryMode = cast(
            MessageDeliveryMode, channel_settings.message_delivery_mode
        )
        self.message_rate: MessageRateMode = cast(
            MessageRateMode, channel_settings.message_rate
        )

    async def commit_to_db(self, session: AsyncSession):
        """Commit current state to database."""
        db_result = await session.execute(
            select(TwitchChannelSettings).where(
                TwitchChannelSettings.id == self.channel_id
            )
        )
        result = db_result.scalar_one_or_none()
        if not result:
            result = TwitchChannelSettings(id=self.channel_id)
            await session.flush()
        result.message_receive_mode = self.message_receive_mode
        result.message_delivery_mode = self.message_delivery_mode
        result.message_rate = self.message_rate
