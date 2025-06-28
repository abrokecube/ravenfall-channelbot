from typing import List, Tuple, Callable, TypeVar, Generic, Optional, Deque, Dict, Any
import asyncio
import time
from collections import deque
from twitchAPI.chat import ChatMessage
from .models import RavenBotMessage, RavenfallMessage

T = TypeVar('T')

class BaseMessageWaiter(Generic[T]):
    """Base class for message waiters that handles a specific message type."""
    
    def __init__(self, max_message_age: float = 5.0):
        """Initialize the message waiter.
        
        Args:
            max_message_age: Maximum age in seconds to keep messages in the queue
        """
        self.waiting_messages: List[Tuple[Callable[[T], bool], asyncio.Future]] = []
        self.message_queue: Deque[Tuple[float, T]] = deque()  # (timestamp, message) pairs
        self.max_message_age = max_message_age
        self._lock = asyncio.Lock()
    
    async def wait_for_message(self, check: Callable[[T], bool], timeout: float = 10.0, 
                             max_age: Optional[float] = None) -> Optional[T]:
        """Wait for a message that matches the check function.
        
        Args:
            check: A function that takes a message and returns True if it matches
            timeout: Maximum time to wait in seconds for a new message
            max_age: Maximum age in seconds for an existing message to be considered.
                   If None, only new messages will be checked.
                   
        Returns:
            The matching message if found, or None if timeout
        """
        # First check existing messages in the queue if max_age is specified
        if max_age is not None:
            current_time = time.time()
            async with self._lock:
                # Process queue in reverse to check newest messages first
                for i in range(len(self.message_queue) - 1, -1, -1):
                    msg_time, message = self.message_queue[i]
                    if current_time - msg_time > max_age:
                        break  # Messages are ordered by time, so we can stop here
                    if check(message):
                        # Remove this message from the queue since it's been consumed
                        del self.message_queue[i]
                        return message
        
        # If no matching message in queue, wait for a new one
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        
        async with self._lock:
            self.waiting_messages.append((check, future))
        
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            # Remove our future if it's still in the list
            async with self._lock:
                self._remove_future(future)
            return None
    
    def _remove_future(self, future: asyncio.Future) -> None:
        """Remove a future from the waiting list if it exists"""
        for i, (_, f) in enumerate(self.waiting_messages):
            if f == future:
                self.waiting_messages.pop(i)
                return
    
    async def process_message(self, message: T) -> None:
        """Process an incoming message and complete any matching futures"""
        current_time = time.time()
        
        # Add message to queue with timestamp
        async with self._lock:
            # Clean up old messages from the queue
            while self.message_queue and current_time - self.message_queue[0][0] > self.max_message_age:
                self.message_queue.popleft()
                
            self.message_queue.append((current_time, message))
            
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


class MessageWaiter(BaseMessageWaiter[ChatMessage]):
    """Waits for and processes Twitch chat messages."""
    pass


class RavenBotMessageWaiter(BaseMessageWaiter[RavenBotMessage]):
    """Waits for and processes messages from RavenBot."""
    
    def __init__(self, max_message_age: float = 5.0):
        super().__init__(max_message_age)
    
    async def wait_for_command(self, command: str, correlation_id: Optional[str] = None, 
                                     timeout: float = 10.0) -> Optional[RavenBotMessage]:
        """Wait for a response to a specific command.
        
        Args:
            command: The command to wait for a response to
            correlation_id: Optional correlation ID to match
            timeout: Maximum time to wait in seconds
            
        Returns:
            The matching message if found, or None if timeout
        """
        def check(msg: RavenBotMessage) -> bool:
            if msg['Identifier'] != command:
                return False
            if correlation_id and msg.get('CorrelationId') != correlation_id:
                return False
            return True
            
        return await self.wait_for_message(check, timeout)


class RavenfallMessageWaiter(BaseMessageWaiter[RavenfallMessage]):
    """Waits for and processes messages from Ravenfall."""
    
    async def wait_for_format_match(self, format_str: str, timeout: float = 10.0) -> Optional[RavenfallMessage]:
        """Wait for a message with a specific format string.
        
        Args:
            format_str: The format string to match
            timeout: Maximum time to wait in seconds
            
        Returns:
            The matching message if found, or None if timeout
        """
        def check(msg: RavenfallMessage) -> bool:
            return msg['Format'] == format_str
            
        return await self.wait_for_message(check, timeout)
