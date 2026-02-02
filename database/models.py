from database import enums
from database.db import engine

from sqlalchemy import (
    Column, String, Integer, ForeignKey, Table, Enum, Boolean, DateTime, Float, JSON
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

import logging

logger = logging.getLogger(__name__)

class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'

    twitch_id = Column(Integer, primary_key=True)
    name_tag_color = Column(String, default="#7F7F7F")
    name = Column(String)
    display_name = Column(String)
    
    characters = relationship("Character", back_populates='user')


class Channel(Base):
    __tablename__ = 'channels'

    id = Column(Integer, primary_key=True)
    name = Column(String)
    idle_earn_rate = Column(Integer, default=5)
    idle_earn_interval = Column(Integer, default=5*60)  # add credits every 5 minutes
    prefix = Column(JSON, nullable=False, default=["!"])
    scroll_queue = Column(JSON, nullable=False, default=[])


class Character(Base):
    __tablename__ = 'characters'
    
    id = Column(String, primary_key=True)

    twitch_id = Column(Integer, ForeignKey('users.twitch_id'))
    training = Column(String, default="None")
    user = relationship("User", back_populates='characters')

    auto_raid_status = relationship("AutoRaidStatus", back_populates='char', uselist=False)
    user_credit_idle_earn = relationship("UserCreditIdleEarn", back_populates='char', uselist=False)


class AutoRaidStatus(Base):
    __tablename__ = 'auto_raid_status'
    
    id = Column(Integer, primary_key=True)
    char_id = Column(String, ForeignKey('characters.id'), unique=True)
    auto_raid_count = Column(Integer, default=-1)
    char = relationship("Character", back_populates='auto_raid_status')

class SenderData(Base):
    __tablename__ = 'sender_data'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_platform = Column(String)
    channel_platform_id = Column(String)
    user_id = Column(String)  # uuid
    character_id = Column(String)  # uuid
    username = Column(String)
    display_name = Column(String)
    color = Column(String, nullable=True)
    platform = Column(String)
    platform_id = Column(String)
    is_broadcaster = Column(Boolean)
    is_moderator = Column(Boolean)
    is_subscriber = Column(Boolean)
    is_vip = Column(Boolean)
    is_game_administrator = Column(Boolean)
    is_game_moderator = Column(Boolean)
    sub_tier = Column(Integer)
    identifier = Column(String)

class TwitchAuth(Base):
    __tablename__ = 'twitch_auth'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer)
    user_name = Column(String)
    access_token = Column(String)
    refresh_token = Column(String)

class UserCredits(Base):
    __tablename__ = 'user_credits'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    credits = Column(Integer, default=0)

class UserCreditIdleEarn(Base):
    __tablename__ = 'user_credit_idle_earn'
    
    id = Column(Integer, primary_key=True)
    char_id = Column(String, ForeignKey('characters.id'), unique=True)
    total_time = Column(Float, default=0)  # in seconds
    last_seen_timestamp = Column(DateTime)
    
    char = relationship('Character', back_populates='user_credit_idle_earn')

class UserCreditTransaction(Base):
    __tablename__ = 'user_credit_transaction'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    credits = Column(Integer)
    description = Column(String)
    timestamp = Column(DateTime)
    
class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    room_name = Column(String, nullable=False)
    user_name = Column(String, nullable=False)
    content = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    reply_to_id = Column(Integer, ForeignKey('chat_messages.id'), nullable=True)
    user_id = Column(String, nullable=True)
    
    
    reply_to = relationship("ChatMessage", remote_side=[id], backref="replies")
    

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
