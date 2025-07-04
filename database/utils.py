from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from database.models import User, Channel, Character
from typing import Union, Optional, Tuple
from sqlalchemy import select


async def get_user(
    session: AsyncSession,
    *, 
    id: Union[int, str] = None,
    name: str = None
):
    if isinstance(id, str):
        id = int(id)
    
    if id:
        result = await session.execute(
            select(User).where(User.twitch_id == id)
        )
    elif name:
        result = await session.execute(
            select(User).where(User.name == name)
        )
    else:
        return None
    
    user_obj = result.scalar_one_or_none()
    if user_obj is None:
        user_obj = User(
            twitch_id=id,
            name=name
        )
        session.add(user_obj)
    if name:
        user_obj.name = name
    return user_obj

async def get_channel(
    session: AsyncSession,
    *, 
    id: Union[int, str] = None,
    name: str = None
):
    if isinstance(id, str):
        id = int(id)

    if id:
        result = await session.execute(
            select(Channel).where(Channel.id == id)
        )
    elif name:
        result = await session.execute(
            select(Channel).where(Channel.name == name)
        )
    else:
        return None
    
    user_obj = result.scalar_one_or_none()
    if user_obj is None:
        user_obj = Channel(
            id=id,
            name=name
        )
        session.add(user_obj)
    if user_obj.name is None:
        user_obj.name = name
    return user_obj

async def get_character(
    session: AsyncSession,
    id: Union[int, str],
    *, 
    twitch_id: Union[int, str] = None,
    name: str = None,
):
    if isinstance(twitch_id, str):
        twitch_id = int(twitch_id)

    result = await session.execute(
        select(Character).where(Character.id == id)
    )
    
    user_obj = result.scalar_one_or_none()
    if user_obj is None:
        user_obj = Character(
            id=id,
            twitch_id=twitch_id
        )
        session.add(user_obj)
    await get_user(session, id=twitch_id, name=name)
    if name:
        user_obj.name = name  # update name if it was changed

    return user_obj

async def record_character_and_user(
    session: AsyncSession,
    # Character fields
    character_id: str,
    twitch_id: Union[int, str],
    # User fields
    user_name: Optional[str] = None,
    name_tag_color: Optional[str] = None
) -> Tuple[User, Character]:
    """
    Create or update both a User and their associated Character in a single transaction.
    
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
    if name_tag_color is not None:
        user.name_tag_color = name_tag_color
    
    # Get or create character
    result = await session.execute(
        select(Character).where(Character.id == character_id)
    )
    character = result.scalar_one_or_none()
    
    if character is None:
        character = Character(
            id=character_id,
            twitch_id=twitch_id
        )
        session.add(character)
    
    # Link character to user
    character.user = user

async def record_user(
    session: AsyncSession,
    user_name: str,
    name_tag_color: Optional[str] = None
) -> User:
    user = await get_user(session, name=user_name)
    if name_tag_color is not None:
        user.name_tag_color = name_tag_color
    return user