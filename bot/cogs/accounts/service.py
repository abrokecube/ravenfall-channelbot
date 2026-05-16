from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from bot.core.components import BaseService
from bot.db.session import get_async_session
from bot.services.config_service import ConfigService
from bot.services.remote_bot import RemoteBotService

from .account import Account
from .config import AccountConfig
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
        self._config: AccountConfig | None = None

    @override
    async def setup(self) -> None:
        """Load configuration during setup."""
        config_service = await self.global_context.wait_for_service(ConfigService)
        self._config = config_service.get_table(AccountConfig)

    def register_merge_handler(self, handler: AccountMergeMixin) -> None:
        """Register a handler to be notified when accounts are merged."""
        if handler not in self._merge_handlers:
            self._merge_handlers.append(handler)
            handler.inject_account_service(self)

    async def _fetch_from_central(
        self, platform: str, platform_id: str
    ) -> AccountLink | None:
        """Fetch account link from central bot.

        Args:
            platform: The platform name.
            platform_id: The platform ID.

        Returns:
            AccountLink if found, else None.
        """
        if (
            not self._config
            or not self._config.sync_enabled
            or not self._config.central_bot_id
        ):
            return None

        try:
            remote_bot_service = self.global_context.get_service(RemoteBotService)
            if not remote_bot_service:
                return None

            central_bot = remote_bot_service.get_remote_bot(
                self._config.central_bot_id
            )
            from bot.cogs.accounts.cog import AccountLinkStruct

            result = await remote_bot_service.call_remote(
                central_bot,
                "AccountCog",
                "get_remote_account_link",
                AccountLinkStruct,
                {"platform": platform, "platform_id": platform_id},
            )

            if result is None:
                return None

            # Convert AccountLinkStruct back to AccountLink
            async with get_async_session() as session:
                # Check if account exists locally
                account_result = await session.execute(
                    select(AccountModel).where(AccountModel.id == result.account_id)
                )
                account = account_result.scalar_one_or_none()

                if not account:
                    # Create account locally
                    account = AccountModel(id=result.account_id)
                    session.add(account)
                    await session.commit()
                    await session.refresh(account)

                # Create link locally
                link = AccountLink(
                    account_id=result.account_id,
                    platform=result.platform,
                    platform_id=result.platform_id,
                    username=result.username,
                    is_primary=result.is_primary,
                )
                session.add(link)
                await session.commit()
                await session.refresh(link)
                session.expunge(link)
                return link

        except (ConnectionError, KeyError, RuntimeError):
            # Central bot unreachable or not configured, fall back to local
            LOGGER.debug("Central bot unreachable, using local data")
            return None

    async def _push_to_central(self, account_id: str, links: list[AccountLink]) -> None:
        """Push account links to central bot.

        Args:
            account_id: The account ID.
            links: List of links to sync.
        """
        if (
            not self._config
            or not self._config.sync_enabled
            or not self._config.central_bot_id
        ):
            return

        try:
            remote_bot_service = self.global_context.get_service(RemoteBotService)
            if not remote_bot_service:
                return

            central_bot = remote_bot_service.get_remote_bot(self._config.central_bot_id)
            from bot.cogs.accounts.cog import AccountLinkStruct, SuccessStruct

            link_structs = [
                AccountLinkStruct(
                    account_id=link.account_id,
                    platform=link.platform,
                    platform_id=link.platform_id,
                    username=link.username,
                    is_primary=link.is_primary,
                )
                for link in links
            ]

            __ = await remote_bot_service.call_remote(
                central_bot,
                "AccountCog",
                "sync_remote_account_links",
                SuccessStruct,
                {"account_id": account_id, "links": link_structs},
            )

        except (ConnectionError, KeyError, RuntimeError):
            LOGGER.debug("Failed to push to central bot")

    async def get_or_create_account(
        self,
        platform: str,
        platform_id: str,
        username: str,
        *,
        overwrite_username: bool = False,
    ) -> Account:
        """Get or create an account by providing platform details.

        Args:
            platform: The platform name (e.g., 'twitch').
            platform_id: The ID on that platform.
            username: The username on that platform.
            overwrite_username: Whether to overwrite the username if it has changed.

        Returns:
            An Account wrapper object.
        """
        async with get_async_session() as session:
            # 1. Try to find existing link locally
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
                if overwrite_username and link.username != username:
                    link.username = username
                    await session.commit()
                account = link.account
                session.expunge(account)
                session.expunge(link)

                # Sync with central if enabled
                if self._config and self._config.sync_enabled:
                    links = await self.get_account_links(account.id)
                    await self._push_to_central(account.id, links)

                return Account(self, account)

            # 2. Not found locally, try central if sync enabled
            if self._config and self._config.sync_enabled:
                central_link = await self._fetch_from_central(platform, platform_id)
                if central_link:
                    # Central found, save to local and return
                    account = await session.execute(
                        select(AccountModel).where(
                            AccountModel.id == central_link.account_id
                        )
                    )
                    account_model = account.scalar_one_or_none()
                    if account_model:
                        session.expunge(account_model)
                        return Account(self, account_model)

            # 3. Not found anywhere, create new Account and Link
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

            # Push to central if sync enabled
            if self._config and self._config.sync_enabled:
                await self._push_to_central(account_id, [new_link])

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

                    # Sync with central if enabled
                    if self._config and self._config.sync_enabled:
                        links = await self.get_account_links(account_id)
                        await self._push_to_central(account_id, links)

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

            # Sync with central if enabled
            if self._config and self._config.sync_enabled:
                links = await self.get_account_links(account_id)
                await self._push_to_central(account_id, links)

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

    async def find_link_by_platform_id(
        self, platform: str, platform_id: str
    ) -> AccountLink | None:
        """Find an account link by platform and platform ID.

        Args:
            platform: The platform name.
            platform_id: The platform ID to search for.

        Returns:
            The AccountLink if found, else None.
        """
        async with get_async_session() as session:
            stmt = select(AccountLink).where(
                AccountLink.platform == platform, AccountLink.platform_id == platform_id
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
            # Note: We might have duplicate links if both accounts had
            # the same platform linked.
            # We'll resolve this by catching UniqueConstraint errors
            # or cleaning up beforehand.

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
