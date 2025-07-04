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
    name_tag_color = Column(String, default="#7F7F7F")
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


async def update_schema():
    """
    Update the database schema by adding any missing columns to existing tables.
    This is a non-destructive operation that only adds missing columns.
    """
    from sqlalchemy import inspect
    
    async with engine.begin() as conn:
        # Create all tables if they don't exist
        await conn.run_sync(Base.metadata.create_all)
        
        # Create an inspector to examine the database
        inspector = inspect(engine.sync_engine)
        
        # Get all tables in the database
        tables = Base.metadata.tables
        
        for table_name, table in tables.items():
            # Get columns that should exist according to our models
            expected_columns = {column.name: column for column in table.columns}
            
            # Get columns that actually exist in the database
            existing_columns = {column['name']: column for column in inspector.get_columns(table_name)}
            
            # Find columns that are in our models but not in the database
            columns_to_add = [
                column for column_name, column in expected_columns.items()
                if column_name not in existing_columns
            ]
            
            # Add missing columns
            for column in columns_to_add:
                column_type = column.type.compile(engine.dialect)
                column_name = column.compile(dialect=engine.dialect)
                
                # Handle column defaults
                default = ''
                if column.default is not None:
                    if column.default.is_scalar:
                        default = f"DEFAULT {column.default.arg}"
                elif not column.nullable and not column.primary_key:
                    if hasattr(column.type, 'length'):
                        default = "DEFAULT ''" if column.type.length else "DEFAULT 0"
                    else:
                        default = "DEFAULT ''"
                
                # Handle nullable
                nullable = 'NULL' if column.nullable or column.primary_key else 'NOT NULL'
                
                # Construct and execute ALTER TABLE statement
                alter_stmt = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type} {default} {nullable}"
                try:
                    await conn.execute(alter_stmt)
                    print(f"Added column {column_name} to table {table_name}")
                except Exception as e:
                    print(f"Error adding column {column_name} to table {table_name}: {e}")
