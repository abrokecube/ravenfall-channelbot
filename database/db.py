# db.py
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = "sqlite+aiosqlite:///data.db"  # use aiosqlite for SQLite

engine = create_async_engine(DATABASE_URL)

