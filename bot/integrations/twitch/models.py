from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, TypedDict, cast

from sqlalchemy import Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column  # noqa: TC002

from bot.db import Base

from .enums import (
    MessageDeliveryMode,
    MessageRateMode,
    MessageReceiveMode,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .enums import (
        EventSubTopic,
    )


class TwitchAuth(Base):
    __tablename__: str = "twitch_auth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer)
    user_name: Mapped[str] = mapped_column(String)
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str] = mapped_column(String)


class TwitchChannelSettings(Base):
    __tablename__: str = "twitch_channel_settings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    message_delivery_mode: Mapped[str] = mapped_column(
        String, default=MessageDeliveryMode.HELIX
    )
    message_receive_mode: Mapped[str] = mapped_column(
        String, default=MessageReceiveMode.IRC
    )
    message_rate: Mapped[str] = mapped_column(String, default=MessageRateMode.STANDARD)


class EventSubChannelTopic(NamedTuple):
    """EventSub channel and topic."""

    channel_id: str | None
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

    def __init__(
        self, channel_settings: TwitchChannelSettings, channel_login: str
    ) -> None:
        self.channel_id: str = channel_settings.id
        self.channel_name: str = channel_login
        self.message_receive_mode: MessageReceiveMode = cast(
            "MessageReceiveMode", channel_settings.message_receive_mode
        )
        self.message_delivery_mode: MessageDeliveryMode = cast(
            "MessageDeliveryMode", channel_settings.message_delivery_mode
        )
        self.message_rate: MessageRateMode = cast(
            "MessageRateMode", channel_settings.message_rate
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
