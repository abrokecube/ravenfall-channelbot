from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    inspect,
    text,
)
from sqlalchemy.engine import Result
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import Mapped, Relationship, mapped_column, relationship
from sqlalchemy.sql.schema import Column, ColumnDefault

from bot.models import QueuedScroll

from . import Base
from .db import engine

logger = logging.getLogger(__name__)


class User(Base):
    __tablename__: str = "users"

    twitch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_tag_color: Mapped[str] = mapped_column(String, default="#7F7F7F")
    name: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)


class UserCredits(Base):
    __tablename__: str = "user_credits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer, default=0)


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
    value: Mapped[bytes] = mapped_column(LargeBinary)


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
