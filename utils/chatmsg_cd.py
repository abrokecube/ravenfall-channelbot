import asyncio
from twitchAPI import chat
import functools
import time
from enum import Enum

class CooldownType(Enum):
    USER = 0
    CHANNEL = 1

def chat_autoresponse_cd(interval_seconds, cd_type: CooldownType = CooldownType.USER):
    """
    Async decorator to throttle calls to an async function with the same arguments.
    """
    cache = {}

    def decorator(func):
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("async_throttle_by_args can only be used on async functions")

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not isinstance(args[0], chat.ChatMessage):
                return TypeError("chatmsg only aga")
            msg: chat.ChatMessage = args[0]
            key = ""
            match cd_type:
                case CooldownType.USER:
                    key = f"{msg.room.name}_{msg.user.name}"
                case CooldownType.CHANNEL:
                    key = f"{msg.room.name}"
            current_time = time.time()
            last_called = cache.get(key, 0)

            if current_time - last_called >= interval_seconds:
                cache[key] = current_time
                return await func(*args, **kwargs)
            else:
                return None  # Or return cached result if you prefer

        return wrapper
    return decorator