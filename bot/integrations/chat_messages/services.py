from __future__ import annotations

import logging
from typing import Any, ClassVar, NamedTuple, override

from bot.core.components import BaseService

LOGGER = logging.getLogger(__name__)


class MessageSendResult(NamedTuple):
    """Message send result."""

    is_sent: bool = True
    reply_id: str | None = None
    drop_reason: str | None = None


class BaseMessageService(BaseService):
    """Base class for message services."""

    _services: ClassVar[dict[str, BaseMessageService]] = {}

    def __init__(self, platform_name: str) -> None:
        super().__init__()
        self.platform: str = platform_name.lower()

    @override
    async def setup(self) -> None:
        if self.platform in self._services:
            msg = f"A service handling the platform '{self.platform}' already exists"
            raise RuntimeError(msg)
        self._services[self.platform] = self

    @override
    async def teardown(self) -> None:
        if self.platform in self._services:
            del self._services[self.platform]

    async def send_message(
        self,
        text: str,  # pyright: ignore[reportUnusedParameter]
        channel_id: str,  # pyright: ignore[reportUnusedParameter]
        *,
        reply_id: str | None = None,  # pyright: ignore[reportUnusedParameter]
        **kwargs: Any,  # pyright: ignore[reportUnusedParameter, reportAny, reportExplicitAny]
    ) -> MessageSendResult:
        """Sends a message.

        Returns the ID of the sent message.
        """
        raise NotImplementedError


class GlobalMessengerService(BaseService):
    """Send messages to any registered platform."""

    async def send(
        self,
        text: str,
        target_platform: str,
        channel_id: str,
        *,
        reply_id: str | None = None,
        **kwargs: Any,  # pyright: ignore[reportAny, reportExplicitAny]
    ) -> MessageSendResult:
        """Send a message to a target platform.

        Returns the ID of the sent message.
        """
        services = BaseMessageService._services
        target_platform = target_platform.lower()
        if target_platform not in services:
            LOGGER.warning(
                f"Target platform {target_platform} does not have a registered service"
            )
            return MessageSendResult(is_sent=False, reply_id=None)
        service = services[target_platform]
        return await service.send_message(text, channel_id, reply_id=reply_id, **kwargs)
