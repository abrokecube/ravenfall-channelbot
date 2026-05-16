from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.cogs.accounts.service import AccountService


class AccountMergeMixin:
    """Mixin that adds account merging support to any class.

    Designed for multiple inheritance, e.g.::

        class MyService(BaseService, AccountMergeMixin):
            ...
    """

    _account_service: AccountService | None = None

    def inject_account_service(self, account_service: AccountService) -> None:
        """Store a reference to the ``AccountService`` and register for merge events.

        Args:
            account_service: The account service instance to use.
        """
        self._account_service = account_service
        account_service.register_merge_handler(self)

    def _require_account_service(self) -> AccountService:
        """Return the injected account service or raise."""
        svc: AccountService | None = getattr(self, "_account_service", None)
        if svc is None:
            msg = "AccountService has not been injected. Call inject_account_service() first."
            raise RuntimeError(msg)
        return svc

    async def on_account_merged(self, source_id: str, dest_id: str) -> None:
        """Called when an account is about to be merged into another.

        Override this method to react to account merges (e.g., migrate data).

        Args:
            source_id: The ID of the account being merged and removed.
            dest_id: The ID of the account that will remain.
        """
