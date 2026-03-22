from sqlalchemy.ext.asyncio import AsyncSession
from .models import (
    User,
    Channel,
    Character,
    SenderData,
    TwitchAuth,
    UserCredits,
    UserCreditTransaction,
    KeyValue,
)
from sqlalchemy import select
from datetime import datetime
from bot.models import Sender, QueuedScroll


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
        raise ValueError("Either id or name must be provided")

    user_obj = result.scalar_one_or_none()
    if user_obj is None:
        user_obj = User(twitch_id=id, name=name, display_name=name)
        session.add(user_obj)
        await session.flush()
    if name:
        user_obj.name = name
    return user_obj


async def get_key_value(
    session: AsyncSession,
    key: str,
    default: object = None,
) -> object:
    result = await session.execute(select(KeyValue).where(KeyValue.key == key))
    item = result.scalar_one_or_none()
    if item is None:
        return default
    return item.value


async def set_key_value(
    session: AsyncSession,
    key: str,
    value: object,
) -> KeyValue:
    result = await session.execute(select(KeyValue).where(KeyValue.key == key))
    item = result.scalar_one_or_none()
    if item is None:
        item = KeyValue(key=key, value=value)
        session.add(item)
    else:
        item.value = value
    return item


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
        raise ValueError("Either id or name must be provided")

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


async def get_sender_data(
    session: AsyncSession, channel_id: int | str, user_name: str
) -> SenderData | None:
    if isinstance(channel_id, int):
        channel_id = str(channel_id)
    result = await session.execute(
        select(SenderData).where(
            SenderData.channel_platform_id == channel_id,
            (SenderData.username == user_name) | (SenderData.display_name == user_name),
        )
    )
    sender_data = result.scalar_one_or_none()
    return sender_data


async def get_formatted_sender_data(
    session: AsyncSession, channel_id: int | str, user_name: str
) -> Sender:
    sender_data = await get_sender_data(session, channel_id, user_name)
    if sender_data is not None:
        return {
            "Id": sender_data.user_id,
            "CharacterId": sender_data.character_id,
            "Username": sender_data.username,
            "DisplayName": sender_data.display_name,
            "Color": sender_data.color,
            "Platform": sender_data.platform,
            "PlatformId": sender_data.platform_id,
            "IsBroadcaster": sender_data.is_broadcaster,
            "IsModerator": sender_data.is_moderator,
            "IsSubscriber": sender_data.is_subscriber,
            "IsVip": sender_data.is_vip,
            "IsGameAdministrator": sender_data.is_game_administrator,
            "IsGameModerator": sender_data.is_game_moderator,
            "SubTier": sender_data.sub_tier,
            "Identifier": sender_data.identifier,
        }
    return {
        "Id": "00000000-0000-0000-0000-000000000000",
        "CharacterId": "00000000-0000-0000-0000-000000000000",
        "Username": user_name,
        "DisplayName": user_name,
        "Color": "#7F7F7F",
        "Platform": "twitch",
        "PlatformId": None,
        "IsBroadcaster": False,
        "IsModerator": False,
        "IsSubscriber": False,
        "IsVip": False,
        "IsGameAdministrator": False,
        "IsGameModerator": False,
        "SubTier": 0,
        "Identifier": "1",
    }


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


async def record_sender_data(
    session: AsyncSession,
    channel_platform: str,
    channel_platform_id: int | str,
    sender_json: Sender,
) -> SenderData:
    if isinstance(channel_platform_id, int):
        channel_platform_id = str(channel_platform_id)
    username = sender_json.get("Username", "")
    if not username:
        sender_data = SenderData()
        session.add(sender_data)
    else:
        result = await get_sender_data(session, channel_platform_id, username)
        if result is None:
            sender_data = SenderData()
            session.add(sender_data)
        else:
            sender_data = result

    sender_data.channel_platform = channel_platform
    sender_data.channel_platform_id = channel_platform_id
    sender_data.user_id = sender_json.get("Id")
    sender_data.character_id = sender_json.get("CharacterId")
    sender_data.username = username.lower()
    sender_data.display_name = sender_json.get("DisplayName")
    sender_data.color = sender_json.get("Color")
    sender_data.platform = sender_json.get("Platform")
    sender_data.platform_id = sender_json.get("PlatformId", "") or ""
    sender_data.is_broadcaster = sender_json.get("IsBroadcaster")
    sender_data.is_moderator = sender_json.get("IsModerator")
    sender_data.is_subscriber = sender_json.get("IsSubscriber")
    sender_data.is_vip = sender_json.get("IsVip")
    sender_data.is_game_administrator = sender_json.get("IsGameAdministrator")
    sender_data.is_game_moderator = sender_json.get("IsGameModerator")
    sender_data.sub_tier = sender_json.get("SubTier")
    sender_data.identifier = sender_json.get("Identifier")

    return sender_data


async def get_twitch_tokens_raw(
    session: AsyncSession, user_id: int | str
) -> TwitchAuth | None:
    result = await session.execute(
        select(TwitchAuth).where(TwitchAuth.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_twitch_tokens(
    session: AsyncSession, user_id: int | str
) -> tuple[str, str] | None:
    a = await get_twitch_tokens_raw(session, user_id)
    if a:
        return str(a.access_token), str(a.refresh_token)
    return None


async def update_twitch_tokens(
    session: AsyncSession,
    user_id: int | str,
    access_token: str,
    refresh_token: str,
    user_name: str,
) -> None:
    result = await get_twitch_tokens_raw(session, user_id)
    if result is None:
        result = TwitchAuth(
            user_id=user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            user_name=user_name,
        )
        session.add(result)
    else:
        result.access_token = access_token
        result.refresh_token = refresh_token
        result.user_name = user_name


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
