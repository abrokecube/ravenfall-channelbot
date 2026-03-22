from . import Base
from .db import engine

from sqlalchemy import (
    String,
    Integer,
    ForeignKey,
    Boolean,
    DateTime,
    Float,
    JSON,
    text,
    inspect,
)
from sqlalchemy.orm import relationship, mapped_column
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import Mapped, Relationship
from sqlalchemy.engine import Result
from sqlalchemy.sql.schema import Column, ColumnDefault
from sqlalchemy.engine.reflection import Inspector
import json

import logging

from typing import TYPE_CHECKING, Any
from collections.abc import Iterable

if TYPE_CHECKING:
    from bot.models import QueuedScroll

logger = logging.getLogger(__name__)


class User(Base):
    __tablename__: str = "users"

    twitch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_tag_color: Mapped[str] = mapped_column(String, default="#7F7F7F")
    name: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)

    characters: Relationship[list["Character"]] = relationship(
        "Character", back_populates="user"
    )


class Channel(Base):
    __tablename__: str = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    idle_earn_rate: Mapped[int] = mapped_column(Integer, default=5)
    idle_earn_interval: Mapped[int] = mapped_column(
        Integer, default=5 * 60
    )  # add credits every 5 minutes
    prefix: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=["!"])
    scroll_queue: Mapped[list[QueuedScroll]] = mapped_column(
        JSON, nullable=False, default=[]
    )


class Character(Base):
    __tablename__: str = "characters"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    twitch_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.twitch_id"))
    training: Mapped[str] = mapped_column(String, default="None")
    user: Relationship["User"] = relationship("User", back_populates="characters")

    auto_raid_status: Relationship["AutoRaidStatus"] = relationship(
        "AutoRaidStatus", back_populates="char", uselist=False
    )
    user_credit_idle_earn: Relationship["UserCreditIdleEarn"] = relationship(
        "UserCreditIdleEarn", back_populates="char", uselist=False
    )


class AutoRaidStatus(Base):
    __tablename__: str = "auto_raid_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    char_id: Mapped[str] = mapped_column(String, ForeignKey("characters.id"), unique=True)
    auto_raid_count: Mapped[int] = mapped_column(Integer, default=-1)
    char: Relationship["Character"] = relationship(
        "Character", back_populates="auto_raid_status"
    )


class SenderData(Base):
    __tablename__: str = "sender_data"

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
    __tablename__: str = "twitch_auth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer)
    user_name: Mapped[str] = mapped_column(String)
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str] = mapped_column(String)


class UserCredits(Base):
    __tablename__: str = "user_credits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer, default=0)


class UserCreditIdleEarn(Base):
    __tablename__: str = "user_credit_idle_earn"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    char_id: Mapped[str] = mapped_column(String, ForeignKey("characters.id"), unique=True)
    total_time: Mapped[float] = mapped_column(Float, default=0)  # in seconds
    last_seen_timestamp: Mapped[DateTime] = mapped_column(DateTime)

    char: Relationship["Character"] = relationship(
        "Character", back_populates="user_credit_idle_earn"
    )


class UserCreditTransaction(Base):
    __tablename__: str = "user_credit_transaction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String)
    timestamp: Mapped[DateTime] = mapped_column(DateTime)


class KeyValue(Base):
    __tablename__: str = "key_value"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[object] = mapped_column(JSON, nullable=True)


class ChatMessage(Base):
    __tablename__: str = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_name: Mapped[str] = mapped_column(String, nullable=False)
    user_name: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    reply_to_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_messages.id"), nullable=True
    )
    user_id: Mapped[str] = mapped_column(String, nullable=True)

    reply_to: Relationship["ChatMessage"] = relationship(
        "ChatMessage", remote_side=[id], backref="replies"
    )


type ColumnMap = dict[str, Column[Any]]
type ExistingColumnMap = dict[str, Any]


async def update_schema() -> None:
    """Update the database schema by adding any missing columns to existing tables.

    This operation is non-destructive: it only creates tables if missing and
    adds columns that do not already exist.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        dialect: str = engine.dialect.name
        tables: dict[str, Any] = Base.metadata.tables

        async with engine.connect() as inspection_conn:
            for table_name, table in tables.items():
                expected_columns: ColumnMap = {c.name: c for c in table.columns}

                existing_columns = await _get_existing_columns(
                    inspection_conn, conn, table_name, dialect
                )

                for column in _missing_columns(expected_columns, existing_columns):
                    await _add_column(conn, table_name, column)


async def _get_existing_columns(
    inspection_conn: AsyncConnection,
    conn: AsyncConnection,
    table_name: str,
    dialect: str,
) -> ExistingColumnMap:
    """Return a mapping of existing column names for a table."""
    if dialect == "sqlite":
        result: Result[Any] = await inspection_conn.execute(
            text(f"PRAGMA table_info({table_name})")
        )
        rows = result.fetchall()
        return {row[1]: row for row in rows}

    if dialect == "postgresql":
        result: Result[Any] = await inspection_conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = :table_name
                """
            ),
            {"table_name": table_name},
        )
        rows = result.fetchall()
        return {row[0]: row for row in rows}

    inspector: Inspector | None = inspect(engine)

    if inspector is None:
        raise ValueError("Inspector is None")

    cols = await conn.run_sync(
        lambda sync_conn: inspector.get_columns(table_name, connection=sync_conn)
    )

    return {c["name"]: c for c in cols}


def _missing_columns(
    expected: ColumnMap,
    existing: ExistingColumnMap,
) -> Iterable[Column[Any]]:
    """Yield columns present in models but missing from the database."""
    for name, column in expected.items():
        if name not in existing:
            yield column


async def _add_column(
    conn: AsyncConnection,
    table_name: str,
    column: Column[Any],
) -> None:
    """Generate and execute an ALTER TABLE statement for a column."""
    column_name: str = column.name
    column_type: str = column.type.compile(engine.dialect)

    default_sql = ""

    if (column.default is not None) and isinstance(column.default, ColumnDefault):
        default: ColumnDefault = column.default
        if default.is_scalar:
            value: Any = default.arg

            if isinstance(column.type, JSON):
                value = f"'{json.dumps(value)}'"
            elif isinstance(value, str):
                value = f"'{value}'"

            default_sql = f"DEFAULT {value}"

        elif default.is_callable:
            default_sql = f"DEFAULT {default.arg()}"

    nullable_sql: str = "NULL" if column.nullable else "NOT NULL"

    stmt: str = (
        f"ALTER TABLE {table_name} "
        f"ADD COLUMN {column_name} {column_type} {default_sql} {nullable_sql}"
    )

    try:
        _ = await conn.execute(text(stmt))
        logger.info("Added column %s to table %s", column_name, table_name)
    except Exception as e:
        logger.error("Error adding column %s to table %s: %s", column_name, table_name, e)
        logger.error("SQL: %s", stmt)
