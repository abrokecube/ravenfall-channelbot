from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from bot.core.components import BaseService
from bot.db.session import get_async_session

from .account import Account
from .models import Account as AccountModel
from .models import AccountLink

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from bot.mixins.account_merge import AccountMergeMixin

LOGGER = logging.getLogger(__name__)


class AccountService(BaseService):
    """Service for managing multi-platform account linking."""

    def __init__(self) -> None:
        super().__init__()
        self._merge_handlers: list[AccountMergeMixin] = []

    def register_merge_handler(self, handler: AccountMergeMixin) -> None:
        """Register a handler to be notified when accounts are merged."""
        if handler not in self._merge_handlers:
            self._merge_handlers.append(handler)
            handler.inject_account_service(self)

    async def get_or_create_account(
        self, platform: str, platform_id: str, username: str
    ) -> Account:
        """Get or create an account by providing platform details.

        Args:
            platform: The platform name (e.g., 'twitch').
            platform_id: The ID on that platform.
            username: The username on that platform.

        Returns:
            An Account wrapper object.
        """
        async with get_async_session() as session:
            # 1. Try to find existing link
            stmt = (
                select(AccountLink)
                .options(selectinload(AccountLink.account))
                .where(
                    AccountLink.platform == platform,
                    AccountLink.platform_id == platform_id,
                )
            )
            result = await session.execute(stmt)
            link = result.scalar_one_or_none()

            if link:
                # Update username if it changed
                if link.username != username:
                    link.username = username
                    await session.commit()
                account = link.account
                session.expunge(account)
                session.expunge(link)
                return Account(self, account)

            # 2. Not found, create new Account and Link
            account_id = str(uuid4())
            account_model = AccountModel(id=account_id)
            session.add(account_model)

            new_link = AccountLink(
                account_id=account_id,
                platform=platform,
                platform_id=platform_id,
                username=username,
                is_primary=True,
            )
            session.add(new_link)

            await session.commit()
            # Refresh to get the fully loaded model and then expunge
            await session.refresh(account_model)
            session.expunge(account_model)
            return Account(self, account_model)

    async def link_account(
        self,
        account_id: str,
        platform: str,
        platform_id: str,
        username: str,
        *,
        is_primary: bool = False,
    ) -> None:
        """Internal method to link a platform to an existing account."""
        async with get_async_session() as session:
            # Check if this platform/id is already linked elsewhere
            check_stmt = select(AccountLink).where(
                AccountLink.platform == platform, AccountLink.platform_id == platform_id
            )
            existing_result = await session.execute(check_stmt)
            existing_link = existing_result.scalar_one_or_none()

            if existing_link:
                if existing_link.account_id == account_id:
                    # Already linked to this account, just update
                    existing_link.username = username
                    if is_primary:
                        await self._set_primary_in_session(
                            session, account_id, platform, platform_id
                        )
                    await session.commit()
                    return
                # Linked to another account - for now, we'll raise an error
                # In the future, this could trigger a merge
                msg = (
                    f"{platform}:{platform_id} is already linked to another account "
                    f"({existing_link.account_id})"
                )
                raise ValueError(msg)

            # Create new link
            new_link = AccountLink(
                account_id=account_id,
                platform=platform,
                platform_id=platform_id,
                username=username,
                is_primary=is_primary,
            )
            session.add(new_link)

            if is_primary:
                await self._set_primary_in_session(
                    session, account_id, platform, platform_id
                )

            await session.commit()

    async def get_account_links(
        self, account_id: str, platform: str | None = None
    ) -> list[AccountLink]:
        """Internal method to get links for an account."""
        async with get_async_session() as session:
            stmt = select(AccountLink).where(AccountLink.account_id == account_id)
            if platform:
                stmt = stmt.where(AccountLink.platform == platform)
            result = await session.execute(stmt)
            links = list(result.scalars().all())
            for link in links:
                session.expunge(link)
            return links

    async def find_link_by_username(
        self, platform: str, username: str
    ) -> AccountLink | None:
        """Find an account link by platform and username.

        Args:
            platform: The platform name.
            username: The username to search for.

        Returns:
            The AccountLink if found, else None.
        """
        async with get_async_session() as session:
            stmt = select(AccountLink).where(
                AccountLink.platform == platform, AccountLink.username == username
            )
            result = await session.execute(stmt)
            link = result.scalar_one_or_none()
            if link:
                session.expunge(link)
            return link

    async def set_primary_link(
        self, account_id: str, platform: str, platform_id: str
    ) -> None:
        """Internal method to set a primary link."""
        async with get_async_session() as session:
            await self._set_primary_in_session(session, account_id, platform, platform_id)
            await session.commit()

    async def _set_primary_in_session(
        self, session: AsyncSession, account_id: str, platform: str, platform_id: str
    ) -> None:
        """Helper to set primary status within a session/transaction."""
        # 1. Set all others for this (account, platform) to False
        __ = await session.execute(
            update(AccountLink)
            .where(AccountLink.account_id == account_id, AccountLink.platform == platform)
            .values(is_primary=False)
        )
        # 2. Set the target one to True
        __ = await session.execute(
            update(AccountLink)
            .where(
                AccountLink.account_id == account_id,
                AccountLink.platform == platform,
                AccountLink.platform_id == platform_id,
            )
            .values(is_primary=True)
        )

    async def merge_accounts(self, source_id: str, dest_id: str) -> None:
        """Merge one account into another.

        This moves all platform links from the source account to the destination account,
        notifies all registered merge handlers, and deletes the source account.

        Args:
            source_id: The ID of the account to merge (this account will be deleted).
            dest_id: The ID of the account to merge into (this account will remain).
        """
        if source_id == dest_id:
            return

        LOGGER.info("Merging account %s into %s", source_id, dest_id)

        # 1. Notify handlers first so they can move their data
        for handler in self._merge_handlers:
            await handler.on_account_merged(source_id, dest_id)

        # 2. Move links and delete source account
        async with get_async_session() as session:
            # Check if destination exists
            dest_result = await session.execute(
                select(AccountModel).where(AccountModel.id == dest_id)
            )
            if not dest_result.scalar_one_or_none():
                msg = f"Destination account {dest_id} does not exist"
                raise ValueError(msg)

            # Move all links from source to dest
            # Note: We might have duplicate links if both accounts had the same platform linked.
            # We'll resolve this by catching UniqueConstraint errors or cleaning up beforehand.

            # For now, we update and handle conflicts.
            links_result = await session.execute(
                select(AccountLink).where(AccountLink.account_id == source_id)
            )
            links = links_result.scalars().all()

            for link in links:
                # Check for conflict in destination
                conflict_stmt = select(AccountLink).where(
                    AccountLink.account_id == dest_id,
                    AccountLink.platform == link.platform,
                    AccountLink.platform_id == link.platform_id,
                )
                conflict_res = await session.execute(conflict_stmt)
                if conflict_res.scalar_one_or_none():
                    # Link already exists in destination, just delete this one
                    await session.delete(link)
                else:
                    link.account_id = dest_id

            # Delete source account
            # Cascades should handle AccountBalance if we set them up,
            # but services usually handle their own.
            source_result = await session.execute(
                select(AccountModel).where(AccountModel.id == source_id)
            )
            source_account = source_result.scalar_one_or_none()
            if source_account:
                await session.delete(source_account)

            await session.commit()
            LOGGER.info("Successfully merged account %s into %s", source_id, dest_id)

    async def delete_account(self, account_id: str) -> None:
        """Delete an account and all its associated links."""
        async with get_async_session() as session:
            stmt = select(AccountModel).where(AccountModel.id == account_id)
            result = await session.execute(stmt)
            account = result.scalar_one_or_none()
            if account:
                await session.delete(account)
                await session.commit()
                LOGGER.info("Deleted account %s", account_id)
