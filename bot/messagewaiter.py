from typing import List, Tuple, Callable
import asyncio
from twitchAPI.chat import ChatMessage

class MessageWaiter:
    def __init__(self):
        self.waiting_messages: List[Tuple[Callable[[ChatMessage], bool], asyncio.Future]] = []
    
    async def wait_for_message(self, check: Callable[[ChatMessage], bool], timeout: float = 10.0):
        """Wait for a message that matches the check function in the specified channel.
        
        Args:
            check: A function that takes a message and returns True if it matches
            timeout: Maximum time to wait in seconds
            
        Returns:
            The matching message if found, or None if timeout
        """
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        
        self.waiting_messages.append((check, future))
        
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            # Remove our future if it's still in the list
            for i, (c, f) in enumerate(self.waiting_messages):
                if f == future:
                    self.waiting_messages.pop(i)
                    break
            return None
    
    async def process_message(self, message: ChatMessage):
        """Process an incoming message and complete any matching futures"""
        if not self.waiting_messages:
            return
            
        remaining = []
        for check, future in self.waiting_messages:
            if not future.done():
                try:
                    if check(message):
                        future.set_result(message)
                    else:
                        remaining.append((check, future))
                except Exception as e:
                    future.set_exception(e)
            
        self.waiting_messages = remaining
