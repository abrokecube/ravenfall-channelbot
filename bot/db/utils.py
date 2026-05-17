from datetime import datetime

from msgspec import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import QueuedScroll

from .models import (
    Channel,
    Character,
    KeyValue,
    User,
    UserCredits,
    UserCreditTransaction,
)


class KeyValueStore:
    def __init__(self, namespace: str):
        self._namespace: str = namespace

    async def set(self, session: AsyncSession, key: str, value: object) -> None:
        """Set a value."""
        full_key = f"{self._namespace}.{key}"
        string_value = json.encode(value)

        result = await session.execute(select(KeyValue).where(KeyValue.key == full_key))
        item = result.scalar_one_or_none()
        if item is None:
            item = KeyValue(key=key, value=string_value)
            session.add(item)
        else:
            item.value = string_value

    async def get[T](
        self, session: AsyncSession, key: str, out_type: type[T], default: T | None = None
    ) -> T | None:
        """Get a value."""
        full_key = f"{self._namespace}.{key}"

        result = await session.execute(select(KeyValue).where(KeyValue.key == full_key))
        item = result.scalar_one_or_none()
        if item is None:
            return default
        return json.decode(item.value, type=out_type)


def get_kv_store(namespace: str) -> KeyValueStore:
    """Get kv store."""
    return KeyValueStore(namespace)


async def get_user(
    session: AsyncSession, *, id: int | str | None = None, name: str | None = None
) -> User:
    if isinstance(id, str):
        id = int(id)

    if id:
        result = await session.execute(select(User).where(User.twitch_id == id))
    elif name:
        result = await session.execute(select(User).where(User.name == name))
    else:
        msg = "Either id or name must be provided"
        raise ValueError(msg)

    user_obj = result.scalar_one_or_none()
    if user_obj is None:
        user_obj = User(twitch_id=id, name=name, display_name=name)
        session.add(user_obj)
        await session.flush()
    if name:
        user_obj.name = name
    return user_obj


async def get_channel(
    session: AsyncSession, *, id: int | str | None = None, name: str | None = None
):
    if isinstance(id, str):
        id = int(id)

    if id:
        result = await session.execute(select(Channel).where(Channel.id == id))
    elif name:
        result = await session.execute(select(Channel).where(Channel.name == name))
    else:
        msg = "Either id or name must be provided"
        raise ValueError(msg)

    user_obj = result.scalar_one_or_none()
    if user_obj is None:
        user_obj = Channel(id=id, name=name)
        session.add(user_obj)
        await session.flush()
    return user_obj


async def get_character(
    session: AsyncSession,
    id: int | str,
    *,
    twitch_id: int | str | None = None,
    name: str | None = None,
) -> Character:
    if isinstance(twitch_id, str):
        twitch_id = int(twitch_id)

    result = await session.execute(select(Character).where(Character.id == id))

    user_obj: Character | None = result.scalar_one_or_none()
    if user_obj is None:
        user_obj = Character(id=id, twitch_id=twitch_id)
        session.add(user_obj)
        await session.flush()
    _ = await get_user(session, id=twitch_id, name=name)
    if name:
        user_obj.user.name = name  # update name if it was changed

    return user_obj


async def record_character_and_user(
    session: AsyncSession,
    # Character fields
    character_id: str,
    twitch_id: int | str,
    # User fields
    user_name: str | None = None,
    display_name: str | None = None,
    name_tag_color: str | None = None,
) -> tuple[User, Character]:
    """Create or update both a User and their associated Character in a single transaction.

    Args:
        session: The database session to use
        character_id: The unique ID of the character
        twitch_id: The Twitch ID of the user (primary key for User table)
        user_name: The display name of the user (optional)
        name_tag_color: The color for the user's name tag (optional)

    Returns:
        A tuple containing (user, character) objects

    """
    # Convert twitch_id to int if it's a string
    if isinstance(twitch_id, str):
        twitch_id = int(twitch_id)

    # Get or create user
    user = await get_user(session, id=twitch_id, name=user_name)

    # Update user fields if provided
    if user_name is not None:
        user.name = user_name
    if display_name is not None:
        user.display_name = display_name
    if name_tag_color is not None:
        user.name_tag_color = name_tag_color

    # Get or create character
    result = await session.execute(select(Character).where(Character.id == character_id))
    character = result.scalar_one_or_none()

    if character is None:
        character = Character(id=character_id, twitch_id=twitch_id)
        session.add(character)

    # Link character to user
    character.user = user
    return user, character


async def record_user(
    session: AsyncSession,
    user_name: str,
    twitch_id: int | str,
    name_tag_color: str | None = None,
    display_name: str | None = None,
) -> User:
    user = await get_user(session, name=user_name, id=twitch_id)
    if name_tag_color is not None:
        user.name_tag_color = name_tag_color
    if display_name is not None:
        user.display_name = display_name
    return user


async def get_user_credits_raw(session: AsyncSession, user_id: int | str) -> UserCredits:
    result = await session.execute(
        select(UserCredits).where(UserCredits.user_id == user_id)
    )
    result_obj = result.scalar_one_or_none()
    if result_obj is None:
        user_credits = UserCredits(user_id=user_id, credits=0)
        session.add(user_credits)
        return user_credits
    return result_obj


async def get_user_credits(session: AsyncSession, user_id: int | str) -> int:
    user_credits = await get_user_credits_raw(session, user_id)
    return user_credits.credits


async def add_credits(
    session: AsyncSession,
    user_id: int | str,
    amount: int,
    description: str = "",
    record_transaction: bool = True,
) -> int:
    user_credits = await get_user_credits_raw(session, user_id)
    user_credits.credits += amount
    if record_transaction:
        transaction = UserCreditTransaction(
            user_id=user_id,
            credits=amount,
            description=description,
            timestamp=datetime.now(),
        )
        session.add(transaction)
        await session.flush()
        return transaction.id
    return -1


async def get_scroll_queue(
    session: AsyncSession, channel_id: int | str
) -> list[QueuedScroll]:
    channel = await get_channel(session, id=channel_id)
    return channel.scroll_queue


async def update_scroll_queue(
    session: AsyncSession, channel_id: int | str, queue: list[QueuedScroll]
):
    channel = await get_channel(session, id=channel_id)
    channel.scroll_queue = queue
