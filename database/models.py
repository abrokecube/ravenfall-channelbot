from database import enums
from database.db import engine

from sqlalchemy import (
    Column, String, Integer, ForeignKey, Table, Enum, Boolean, DateTime, Float, JSON
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'

    twitch_id = Column(Integer, primary_key=True)
    name = Column(String)
    
    characters = relationship("Character", back_populates='user')


class Channel(Base):
    __tablename__ = 'channels'

    id = Column(Integer, primary_key=True)
    name = Column(String)
    prefix = Column(JSON, nullable=False, default=["?"])


class Character(Base):
    __tablename__ = 'characters'
    
    id = Column(String, primary_key=True)
    name = Column(String)

    twitch_id = Column(Integer, ForeignKey('users.twitch_id'))
    user = relationship("User", back_populates='characters')

    auto_raid_status = relationship("AutoRaidStatus", back_populates='char', uselist=False)


class AutoRaidStatus(Base):
    __tablename__ = 'auto_raid_status'
    
    id = Column(Integer, primary_key=True)
    char_id = Column(String, ForeignKey('characters.id'), unique=True)
    auto_raid_count = Column(Integer, default=-1)
    char = relationship("Character", back_populates='auto_raid_status')


async def create_all_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
