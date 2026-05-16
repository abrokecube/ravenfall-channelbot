from __future__ import annotations

from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from .models import Account as AccountModel
    from .models import AccountLink
    from .service import AccountService


class Account:
    """Wrapper for the Account model providing account operations."""

    def __init__(self, service: AccountService, model: AccountModel):
        self._service: AccountService = service
        self._model: AccountModel = model
        self.id: str = model.id

    # id is now an attribute defined in __init__

    async def link_platform(
        self,
        platform: str,
        platform_id: str,
        username: str,
        *,
        is_primary: bool = False,
    ) -> None:
        """Link a platform account to this global account.

        Args:
            platform: The platform name (e.g., 'twitch', 'telegram').
            platform_id: The ID on that platform.
            username: The username on that platform.
            is_primary: Whether this should be the primary account for the platform.
        """
        await self._service.link_account(
            self.id, platform, platform_id, username, is_primary=is_primary
        )

    async def get_links(self, platform: str | None = None) -> list[AccountLink]:
        """Get all links for this account, optionally filtered by platform.

        Args:
            platform: Optional platform name to filter by.

        Returns:
            A list of AccountLink objects.
        """
        return await self._service.get_account_links(self.id, platform)

    async def set_primary(self, platform: str, platform_id: str) -> None:
        """Set a specific platform link as primary for that platform.

        Args:
            platform: The platform name.
            platform_id: The ID on that platform.
        """
        await self._service.set_primary_link(self.id, platform, platform_id)

    async def get_primary(self, platform: str) -> AccountLink | None:
        """Get the primary link for a specific platform.

        Args:
            platform: The platform name.

        Returns:
            The primary AccountLink if found, else None.
        """
        links = await self.get_links(platform)
        for link in links:
            if link.is_primary:
                return link
        return links[0] if links else None

    @override
    def __repr__(self) -> str:
        return f"<Account id={self.id}>"
