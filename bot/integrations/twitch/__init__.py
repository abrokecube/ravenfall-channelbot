from bot.db import Base
from sqlalchemy.orm import mapped_column, Mapped
from sqlalchemy import Integer, String

from .enums import MessageDeliveryMode, MessageReceiveMode, MessageRateMode

EVENT_SOURCE_TWITCH = "twitch"


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
