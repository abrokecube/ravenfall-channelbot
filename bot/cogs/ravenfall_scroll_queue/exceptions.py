from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.cogs.ravenfall_scroll_queue.cog import ScrollQueue


class QueueFullError(Exception):
    """Raised when trying to add an item to a full queue."""

    def __init__(self, message: str, queue: ScrollQueue) -> None:
        self.message: str = message
        self.queue: ScrollQueue = queue
        super().__init__(self.message)


class OutOfStockError(Exception):
    """Raised when a scroll type is out of stock.

    Attributes:
        message (str): The error message.
        queue (ScrollQueue): The scroll queue instance.
    """

    def __init__(self, message: str, queue: ScrollQueue) -> None:
        """Initializes the OutOfStockError.

        Args:
            message: The error message.
            queue: The scroll queue instance.
        """
        self.message: str = message
        self.queue: ScrollQueue = queue
        super().__init__(self.message)


class InsufficientQueueSpaceError(Exception):
    """Raised when the queue does not have enough space for the scroll.

    Attributes:
        message (str): The error message.
        queue (ScrollQueue): The scroll queue instance.
    """

    def __init__(self, message: str, queue: ScrollQueue) -> None:
        """Initializes the InsufficientQueueSpaceError.

        Args:
            message: The error message.
            queue: The scroll queue instance.
        """
        self.message: str = message
        self.queue: ScrollQueue = queue
        super().__init__(self.message)
