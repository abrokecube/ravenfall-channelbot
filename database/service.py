from typing import Any
from database.session import get_async_session
import database.utils as db_utils
from database.models import User, Channel, Character, SenderData, TwitchAuth, UserCredits
from sqlalchemy.ext.asyncio import AsyncSession

class DatabaseService:
    def get_session(self):
        """Returns the async_session context manager so it can be used directly if needed."""
        return get_async_session()

    async def get_user(self, *, id: int | str | None = None, name: str | None = None, session: AsyncSession | None = None) -> User | None:
        if session:
            return await db_utils.get_user(session, id=id, name=name) 
        async with get_async_session() as s:
            return await db_utils.get_user(s, id=id, name=name) 

    async def get_channel(self, *, id: int | str | None = None, name: str | None = None, session: AsyncSession | None = None) -> Channel | None:
        if session:
            return await db_utils.get_channel(session, id=id, name=name) 
        async with get_async_session() as s:
            return await db_utils.get_channel(s, id=id, name=name) 

    async def get_character(self, id: int | str, *, twitch_id: int | str | None = None, name: str | None = None, session: AsyncSession | None = None) -> Character | None:
        if session:
            return await db_utils.get_character(session, id=id, twitch_id=twitch_id, name=name) 
        async with get_async_session() as s:
            return await db_utils.get_character(s, id=id, twitch_id=twitch_id, name=name) 

    async def get_sender_data(self, channel_id: int | str, user_name: str, session: AsyncSession | None = None) -> SenderData | None:
        if session:
            return await db_utils.get_sender_data(session, channel_id, user_name)
        async with get_async_session() as s:
            return await db_utils.get_sender_data(s, channel_id, user_name)

    async def get_formatted_sender_data(self, channel_id: int | str, user_name: str, session: AsyncSession | None = None) -> dict[str, Any]:  # Complex return type from db_utils
        if session:
            return await db_utils.get_formatted_sender_data(session, channel_id, user_name)
        async with get_async_session() as s:
            return await db_utils.get_formatted_sender_data(s, channel_id, user_name)

    async def record_character_and_user(
        self,
        character_id: str,
        twitch_id: int | str,
        user_name: str | None = None,
        display_name: str | None = None,
        name_tag_color: str | None = None,
        session: AsyncSession | None = None
    ) -> tuple[User, Character]:
        if session:
            return await db_utils.record_character_and_user(
                session, character_id, twitch_id, user_name, display_name, name_tag_color
            )
        async with get_async_session() as s:
            return await db_utils.record_character_and_user(
                s, character_id, twitch_id, user_name, display_name, name_tag_color
            )

    async def record_user(
        self,
        user_name: str,
        twitch_id: int | str,
        name_tag_color: str | None = None,
        display_name: str | None = None,
        session: AsyncSession | None = None
    ) -> User:
        if session:
            return await db_utils.record_user(session, user_name, twitch_id, name_tag_color, display_name)
        async with get_async_session() as s:
            return await db_utils.record_user(s, user_name, twitch_id, name_tag_color, display_name)

    async def record_sender_data(
        self,
        channel_platform: str,
        channel_platform_id: int | str,
        sender_json: dict[str, Any],
        session: AsyncSession | None = None
    ) -> SenderData:
        if session:
            return await db_utils.record_sender_data(session, channel_platform, channel_platform_id, sender_json) 
        async with get_async_session() as s:
            return await db_utils.record_sender_data(s, channel_platform, channel_platform_id, sender_json) 

    async def get_tokens_raw(self, user_id: int | str, session: AsyncSession | None = None) -> TwitchAuth | None:
        if session:
            return await db_utils.get_tokens_raw(session, user_id)
        async with get_async_session() as s:
            return await db_utils.get_tokens_raw(s, user_id)

    async def get_tokens(self, user_id: int | str, session: AsyncSession | None = None) -> tuple[str | None, str | None]:
        if session:
            return await db_utils.get_tokens(session, user_id)
        async with get_async_session() as s:
            return await db_utils.get_tokens(s, user_id)

    async def update_tokens(self, user_id: int | str, access_token: str, refresh_token: str, user_name: str, session: AsyncSession | None = None) -> None:
        if session:
            await db_utils.update_tokens(session, user_id, access_token, refresh_token, user_name)
        else:
            async with get_async_session() as s:
                await db_utils.update_tokens(s, user_id, access_token, refresh_token, user_name)

    async def get_user_credits_raw(self, user_id: int | str, session: AsyncSession | None = None) -> UserCredits:
        if session:
            return await db_utils.get_user_credits_raw(session, user_id)
        async with get_async_session() as s:
            return await db_utils.get_user_credits_raw(s, user_id)

    async def get_user_credits(self, user_id: int | str, session: AsyncSession | None = None) -> int:
        if session:
            return await db_utils.get_user_credits(session, user_id)
        async with get_async_session() as s:
            return await db_utils.get_user_credits(s, user_id)

    async def add_credits(self, user_id: int | str, amount: int, description: str = "", record_transaction: bool = True, session: AsyncSession | None = None) -> int:
        if session:
            return await db_utils.add_credits(session, user_id, amount, description, record_transaction)
        async with get_async_session() as s:
            return await db_utils.add_credits(s, user_id, amount, description, record_transaction)

    async def get_scroll_queue(self, channel_id: int | str, session: AsyncSession | None = None) -> list[int]:
        if session:
            return await db_utils.get_scroll_queue(session, channel_id)
        async with get_async_session() as s:
            return await db_utils.get_scroll_queue(s, channel_id)

    async def update_scroll_queue(self, channel_id: int | str, queue: list[int], session: AsyncSession | None = None):
        if session:
            await db_utils.update_scroll_queue(session, channel_id, queue)
        else:
            async with get_async_session() as s:
                await db_utils.update_scroll_queue(s, channel_id, queue)
