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

LOGGER = logging.getLogger(__name__)


class AccountService(BaseService):
    """Service for managing multi-platform account linking."""

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
                session.expunge(link.account)
                return Account(self, link.account)

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
