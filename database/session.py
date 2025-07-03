# db_session.py
from contextlib import asynccontextmanager
from database.db import engine
from sqlalchemy.ext.asyncio import AsyncSession
from typing import AsyncGenerator

@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine) as session:
        try:
            yield session
            await session.commit()
        except:
            await session.rollback()
            raise
        finally:
            await session.close()