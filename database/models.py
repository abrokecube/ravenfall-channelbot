from database.db import engine

from sqlalchemy import (
    String, Integer, ForeignKey, Boolean, DateTime, Float, JSON
)
from sqlalchemy.orm import relationship, mapped_column
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, Relationship

import logging

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.models import QueuedScroll

logger = logging.getLogger(__name__)

class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__: str = 'users'

    twitch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_tag_color: Mapped[str] = mapped_column(String, default="#7F7F7F")
    name: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    
    characters: Relationship[list["Character"]] = relationship("Character", back_populates='user')


class Channel(Base):
    __tablename__: str = 'channels'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    idle_earn_rate: Mapped[int] = mapped_column(Integer, default=5)
    idle_earn_interval: Mapped[int] = mapped_column(Integer, default=5*60)  # add credits every 5 minutes
    prefix: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=["!"])
    scroll_queue: Mapped[list[QueuedScroll]] = mapped_column(JSON, nullable=False, default=[])


class Character(Base):
    __tablename__: str = 'characters'
    
    id: Mapped[str] = mapped_column(String, primary_key=True)

    twitch_id: Mapped[int] = mapped_column(Integer, ForeignKey('users.twitch_id'))
    training: Mapped[str] = mapped_column(String, default="None")
    user: Relationship["User"] = relationship("User", back_populates='characters')

    auto_raid_status: Relationship["AutoRaidStatus"] = relationship("AutoRaidStatus", back_populates='char', uselist=False)
    user_credit_idle_earn: Relationship["UserCreditIdleEarn"] = relationship("UserCreditIdleEarn", back_populates='char', uselist=False)


class AutoRaidStatus(Base):
    __tablename__: str = 'auto_raid_status'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    char_id: Mapped[str] = mapped_column(String, ForeignKey('characters.id'), unique=True)
    auto_raid_count: Mapped[int] = mapped_column(Integer, default=-1)
    char: Relationship["Character"] = relationship("Character", back_populates='auto_raid_status')

class SenderData(Base):
    __tablename__: str = 'sender_data'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_platform: Mapped[str] = mapped_column(String)
    channel_platform_id: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(String)  # uuid
    character_id: Mapped[str] = mapped_column(String)  # uuid
    username: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    color: Mapped[str] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String)
    platform_id: Mapped[str] = mapped_column(String)
    is_broadcaster: Mapped[bool] = mapped_column(Boolean)
    is_moderator: Mapped[bool] = mapped_column(Boolean)
    is_subscriber: Mapped[bool] = mapped_column(Boolean)
    is_vip: Mapped[bool] = mapped_column(Boolean)
    is_game_administrator: Mapped[bool] = mapped_column(Boolean)
    is_game_moderator: Mapped[bool] = mapped_column(Boolean)
    sub_tier: Mapped[int] = mapped_column(Integer)
    identifier: Mapped[str] = mapped_column(String)

class TwitchAuth(Base):
    __tablename__: str = 'twitch_auth'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer)
    user_name: Mapped[str] = mapped_column(String)
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str] = mapped_column(String)

class UserCredits(Base):
    __tablename__: str = 'user_credits'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer, default=0)

class UserCreditIdleEarn(Base):
    __tablename__: str = 'user_credit_idle_earn'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    char_id: Mapped[str] = mapped_column(String, ForeignKey('characters.id'), unique=True)
    total_time: Mapped[float] = mapped_column(Float, default=0)  # in seconds
    last_seen_timestamp: Mapped[DateTime] = mapped_column(DateTime)
    
    char: Relationship["Character"] = relationship('Character', back_populates='user_credit_idle_earn')

class UserCreditTransaction(Base):
    __tablename__: str = 'user_credit_transaction'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String)
    timestamp: Mapped[DateTime] = mapped_column(DateTime)
    
class ChatMessage(Base):
    __tablename__: str = 'chat_messages'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_name: Mapped[str] = mapped_column(String, nullable=False)
    user_name: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    reply_to_id: Mapped[int] = mapped_column(Integer, ForeignKey('chat_messages.id'), nullable=True)
    user_id: Mapped[str] = mapped_column(String, nullable=True)
    
    
    reply_to: Relationship["ChatMessage"] = relationship("ChatMessage", remote_side=[id], backref="replies")
    

async def create_all_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def update_schema():
    """
    Update the database schema by adding any missing columns to existing tables.
    This is a non-destructive operation that only adds missing columns.
    """
    from sqlalchemy import inspect, text
    
    async with engine.begin() as conn:
        # Create all tables if they don't exist
        await conn.run_sync(Base.metadata.create_all)
        
        # Get all tables in the metadata
        tables = Base.metadata.tables
        
        # Use a raw connection for inspection
        async with engine.connect() as inspection_conn:
            # Get the dialect-specific SQL for checking if a column exists
            dialect = engine.dialect.name
            
            for table_name, table in tables.items():
                # Get columns that should exist according to our models
                expected_columns = {column.name: column for column in table.columns}
                
                # Get existing columns from the database
                if dialect == 'sqlite':
                    # SQLite specific query
                    result = await inspection_conn.execute(
                        text(f"PRAGMA table_info({table_name})")
                    )
                    existing_columns = {row[1]: row for row in result.fetchall()}
                elif dialect == 'postgresql':
                    # PostgreSQL specific query
                    result = await inspection_conn.execute(
                        text("""
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = :table_name
                        """),
                        {'table_name': table_name}
                    )
                    existing_columns = {row[0]: row for row in result.fetchall()}
                else:
                    # Fallback for other databases
                    inspector = inspect(engine)
                    existing_columns = {
                        col['name']: col 
                        for col in await conn.run_sync(
                            lambda conn: inspector.get_columns(table_name, connection=conn)
                        )
                    }
                
                # Find columns that are in our models but not in the database
                columns_to_add = [
                    column for column_name, column in expected_columns.items()
                    if column_name not in existing_columns
                ]
                
                # Add missing columns
                for column in columns_to_add:
                    column_type = column.type.compile(engine.dialect)
                    column_name = column.name  # Get the raw column name without table prefix
                    
                    # Handle column defaults
                    default = ""
                    if column.default is not None:
                        if column.default.is_scalar:
                            # Properly quote string literals in SQL
                            default_value = column.default.arg
                            
                            is_json = False
                            if isinstance(column.type, JSON):
                                is_json = True
                            
                            if is_json:
                                import json
                                default_value = f"'{json.dumps(default_value)}'"
                            elif isinstance(default_value, str):
                                default_value = f"'{default_value}'"
                            
                            default = f"DEFAULT {default_value}"
                        elif column.default.is_callable:
                            default = f"DEFAULT {column.default.arg()}"
                    
                    # Handle NULL/NOT NULL
                    nullable = "NULL" if column.nullable else "NOT NULL"
                    
                    # Build and execute the ALTER TABLE statement
                    alter_stmt = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type} {default} {nullable}"
                    
                    try:
                        await conn.execute(text(alter_stmt))
                        logger.info(f"Added column {column_name} to table {table_name}")
                    except Exception as e:
                        logger.error(f"Error adding column {column_name} to table {table_name}: {e}")
                        logger.error(f"SQL: {alter_stmt}")
