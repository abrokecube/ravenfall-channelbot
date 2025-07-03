from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from database.models import User, Channel, Character
from typing import Union
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
            select(User).where(User.id == id)
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
            id=id,
            name=name
        )
        session.add(user_obj)
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
    *, 
    id: Union[int, str] = None,
    twitch_id: str = None,
    name: str = None,
):
    if isinstance(id, str):
        id = int(id)

    if id:
        result = await session.execute(
            select(Character).where(Character.id == id)
        )
    elif name:
        result = await session.execute(
            select(Character).where(Character.name == name)
        )
    elif twitch_id:
        result = await session.execute(
            select(Character).where(Character.twitch_id == twitch_id)
        )
    else:
        return None
    
    user_obj = result.scalar_one_or_none()
    if user_obj is None:
        user_obj = Character(
            id=id,
            name=name,
            twitch_id=twitch_id
        )
        session.add(user_obj)

    user_obj.name = name  # update name if it was changed

    return user_obj