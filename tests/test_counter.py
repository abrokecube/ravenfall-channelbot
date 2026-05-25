import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from bot.db.models import Base
from bot.db.utils import get_kv_store, get_counter

async def main() -> None:
    # Use in-memory SQLite database
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    
    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with AsyncSession(test_engine) as session:
        # Test KeyValueStore namespace bugfix
        kv1 = get_kv_store("ns1")
        kv2 = get_kv_store("ns2")
        
        await kv1.set(session, "mykey", {"a": 1})
        await kv2.set(session, "mykey", {"b": 2})
        await session.commit()
        
        # Verify namespaces are separated
        val1 = await kv1.get(session, "mykey", dict)
        val2 = await kv2.get(session, "mykey", dict)
        print(f"KeyValueStore ns1: {val1}")
        print(f"KeyValueStore ns2: {val2}")
        assert val1 == {"a": 1}
        assert val2 == {"b": 2}
        
        # Test Counter
        c1 = get_counter("cnt1")
        c2 = get_counter("cnt2")
        
        # get default
        assert await c1.get(session, "hits") == 0
        assert await c1.get(session, "hits", default=5) == 5
        
        # set
        await c1.set(session, "hits", 10)
        assert await c1.get(session, "hits") == 10
        
        # increment
        new_val = await c1.increment(session, "hits")
        assert new_val == 11
        assert await c1.get(session, "hits") == 11
        
        new_val_by_5 = await c1.increment(session, "hits", 5)
        assert new_val_by_5 == 16
        assert await c1.get(session, "hits") == 16
        
        # decrement
        dec_val = await c1.decrement(session, "hits")
        assert dec_val == 15
        
        dec_val_by_10 = await c1.decrement(session, "hits", 10)
        assert dec_val_by_10 == 5
        
        # verify cnt2 does not affect cnt1
        assert await c2.get(session, "hits") == 0
        await c2.increment(session, "hits", 100)
        assert await c1.get(session, "hits") == 5
        assert await c2.get(session, "hits") == 100
        
        await session.commit()
        
    print("All tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
