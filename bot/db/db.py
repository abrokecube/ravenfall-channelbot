# db.py
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = "sqlite+aiosqlite:///data.db"  # use aiosqlite for SQLite

engine = create_async_engine(DATABASE_URL)


async def enable_wal_mode() -> None:
    """Enable SQLite WAL mode for the configured database."""
    async with engine.begin() as conn:
        _ = await conn.execute(text("PRAGMA journal_mode=WAL"))
