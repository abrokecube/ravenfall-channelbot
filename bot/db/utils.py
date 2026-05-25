from msgspec import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    KeyValue,
)


class KeyValueStore:
    def __init__(self, namespace: str) -> None:
        self._namespace: str = namespace

    async def set(self, session: AsyncSession, key: str, value: object) -> None:
        """Set a value."""
        full_key = f"{self._namespace}.{key}"
        string_value = json.encode(value)

        result = await session.execute(select(KeyValue).where(KeyValue.key == full_key))
        item = result.scalar_one_or_none()
        if item is None:
            item = KeyValue(key=full_key, value=string_value)
            session.add(item)
        else:
            item.value = string_value

    async def get[T](
        self, session: AsyncSession, key: str, out_type: type[T], default: T | None = None
    ) -> T | None:
        """Get a value."""
        full_key = f"{self._namespace}.{key}"

        result = await session.execute(select(KeyValue).where(KeyValue.key == full_key))
        item = result.scalar_one_or_none()
        if item is None:
            return default
        return json.decode(item.value, type=out_type)


def get_kv_store(namespace: str) -> KeyValueStore:
    """Get kv store."""
    return KeyValueStore(namespace)


class Counter:
    """A counter utility that uses the KeyValue store to keep track of integer counts.

    Like KeyValueStore, it namespaces keys.
    """

    def __init__(self, namespace: str) -> None:
        """Initialize the Counter with a namespace.

        Args:
            namespace: The namespace prefix for all counter keys.
        """
        self._namespace: str = f"counter.{namespace}"

    async def get(self, session: AsyncSession, key: str, default: int = 0) -> int:
        """Get the current count for a key.

        Args:
            session: The database session.
            key: The counter key.
            default: The default value if the counter does not exist. Defaults to 0.

        Returns:
            The current count.
        """
        full_key = f"{self._namespace}.{key}"
        result = await session.execute(select(KeyValue).where(KeyValue.key == full_key))
        item = result.scalar_one_or_none()
        if item is None:
            return default
        return json.decode(item.value, type=int)

    async def set(self, session: AsyncSession, key: str, value: int) -> None:
        """Set the count for a key.

        Args:
            session: The database session.
            key: The counter key.
            value: The integer value to set.
        """
        full_key = f"{self._namespace}.{key}"
        encoded_value = json.encode(value)

        result = await session.execute(select(KeyValue).where(KeyValue.key == full_key))
        item = result.scalar_one_or_none()
        if item is None:
            item = KeyValue(key=full_key, value=encoded_value)
            session.add(item)
        else:
            item.value = encoded_value

    async def increment(self, session: AsyncSession, key: str, amount: int = 1) -> int:
        """Increment the count for a key.

        Args:
            session: The database session.
            key: The counter key.
            amount: The amount to increment by. Defaults to 1.

        Returns:
            The new count.
        """
        full_key = f"{self._namespace}.{key}"
        result = await session.execute(select(KeyValue).where(KeyValue.key == full_key))
        item = result.scalar_one_or_none()
        if item is None:
            new_value = amount
            encoded_value = json.encode(new_value)
            item = KeyValue(key=full_key, value=encoded_value)
            session.add(item)
        else:
            current_value = json.decode(item.value, type=int)
            new_value = current_value + amount
            item.value = json.encode(new_value)
        return new_value

    async def decrement(self, session: AsyncSession, key: str, amount: int = 1) -> int:
        """Decrement the count for a key.

        Args:
            session: The database session.
            key: The counter key.
            amount: The amount to decrement by. Defaults to 1.

        Returns:
            The new count.
        """
        return await self.increment(session, key, -amount)


def get_counter(namespace: str) -> Counter:
    """Get a Counter instance.

    Args:
        namespace: The namespace prefix for all counter keys.

    Returns:
        A Counter instance.
    """
    return Counter(namespace)
