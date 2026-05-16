from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from msgspec import Struct

from bot.core.components import Cog
from bot.services.config_service import ConfigService
from bot.services.remote_bot import RemoteCallableMixin, remote_callable

from .config import AccountConfig
from .service import AccountService

if TYPE_CHECKING:
    from bot.core.components import EventManager

LOGGER = logging.getLogger(__name__)


class AccountLinkStruct(Struct):
    """Remote representation of an account link."""

    account_id: str
    platform: str
    platform_id: str
    username: str
    is_primary: bool


class SuccessStruct(Struct):
    """Success status for remote operations."""

    success: bool


class AccountCog(Cog, RemoteCallableMixin):
    """Cog for managing multi-platform account linking with cross-bot synchronization."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self._config: AccountConfig | None = None

    @override
    async def setup(self) -> None:
        """Register AccountService and set up handlers."""
        # Load configuration
        config_service = await self.global_context.wait_for_service(ConfigService)
        self._config = config_service.get_table(AccountConfig)

        # Instantiate and register AccountService
        service = AccountService()
        await self.global_context.register_service(service)
        LOGGER.info("AccountCog and AccountService started")

    # --- Remote Callables ---

    @remote_callable(AccountLinkStruct | None)
    async def get_remote_account_link(
        self, platform: str, platform_id: str
    ) -> AccountLinkStruct | None:
        """Fetch a link and its associated account ID from the central registry.

        Args:
            platform: The platform name (e.g., 'twitch').
            platform_id: The ID on that platform.

        Returns:
            AccountLinkStruct if found, else None.
        """
        account_service = self.global_context.require_service(AccountService)
        link = await account_service.find_link_by_platform_id(platform, platform_id)

        if link is None:
            return None

        return AccountLinkStruct(
            account_id=link.account_id,
            platform=link.platform,
            platform_id=link.platform_id,
            username=link.username,
            is_primary=link.is_primary,
        )

    @remote_callable(SuccessStruct)
    async def sync_remote_account_links(
        self, account_id: str, links: list[AccountLinkStruct]
    ) -> SuccessStruct:
        """Receive links from a local bot and merge into central account registry.

        Args:
            account_id: The account ID to sync links for.
            links: List of account link structures to merge.

        Returns:
            SuccessStruct indicating operation status.
        """
        account_service = self.global_context.require_service(AccountService)

        for link_struct in links:
            try:
                await account_service.link_account(
                    account_id=account_id,
                    platform=link_struct.platform,
                    platform_id=link_struct.platform_id,
                    username=link_struct.username,
                    is_primary=link_struct.is_primary,
                )
            except ValueError:
                # Link already exists or conflicts - skip
                continue

        return SuccessStruct(success=True)
