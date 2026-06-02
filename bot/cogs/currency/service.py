from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, override

from msgspec import Struct
from sqlalchemy import select, update

from bot.core.components import BaseService
from bot.db.session import get_async_session
from bot.mixins.account_merge import AccountMergeMixin
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.remote_bot import RemoteBotService

from .config import CurrencyConfig
from .models import AccountBalance, TransactionHistory

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

LOGGER = logging.getLogger(__name__)


class RemoteBalance(Struct):
    """Balance data from a remote bot."""

    balance: int


class RemoteHistoryItem(Struct):
    """History item from a remote bot."""

    amount: int
    reason: str
    timestamp: str  # ISO format


class RemoteHistory(Struct):
    """Transaction history from a remote bot."""

    items: list[RemoteHistoryItem]


class CombinedBalance(Struct):
    """Aggregated balance across all bots."""

    total: int
    local: int
    remote: dict[str, int]


class CombinedHistoryItem(Struct):
    """Aggregated history item across all bots."""

    amount: int
    reason: str
    timestamp: datetime.datetime
    bot_name: str


class CurrencyService(BaseService, ConfigSubscriberMixin, AccountMergeMixin):
    """Service for managing user currency balances and transactions."""

    def __init__(self) -> None:
        super().__init__()
        self.config: CurrencyConfig | None = None

    @override
    async def setup(self) -> None:
        from bot.cogs.accounts.service import AccountService
        from bot.services.config_service import ConfigService

        config_srv = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_srv)
        self.config = self.subscribe_config(CurrencyConfig)

        account_service = await self.global_context.wait_for_service(AccountService)
        self.inject_account_service(account_service)

    @override
    async def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if isinstance(config, CurrencyConfig):
            self.config = config
            LOGGER.info("Currency configuration updated")

    def get_currency_name(self, amount: int) -> str:
        """Get the singular or plural name of the currency based on amount."""
        if not self.config:
            return "Coins"
        return self.config.name_singular if abs(amount) == 1 else self.config.name_plural

    async def get_combined_balance(
        self, account_id: str, db_session: AsyncSession
    ) -> CombinedBalance:
        """Get balance across this bot and all remote bots."""
        from bot.cogs.accounts.service import AccountService
        from bot.services.remote_bot import RemoteBotService

        local_balance = await self.get_balance(account_id, db_session)
        remote_balances: dict[str, int] = {}
        total = local_balance

        if not self.config or not self.config.remote_enabled:
            return CombinedBalance(total=total, local=local_balance, remote={})

        remote_bot_service = self.global_context.get_service(RemoteBotService)
        if not remote_bot_service:
            return CombinedBalance(total=total, local=local_balance, remote={})

        account_service = self.global_context.require_service(AccountService)
        links = await account_service.get_account_links(db_session, account_id)

        if not links:
            return CombinedBalance(total=total, local=local_balance, remote={})

        # We take the first primary link or just the first link
        # to identify the user remotely
        primary_link = next((li for li in links if li.is_primary), links[0])

        # Call all remote bots

        for bot_name, bot in remote_bot_service.remote_bots.items():
            try:
                # Use the cog name 'CurrencyCog' (will be defined in cog.py)
                remote_bal = await remote_bot_service.call_remote(
                    bot,
                    "CurrencyCog",
                    "get_remote_balance",
                    RemoteBalance,
                    kwargs={
                        "platform": primary_link.platform,
                        "platform_id": primary_link.platform_id,
                    },
                )
                remote_balances[bot_name] = remote_bal.balance
                total += remote_bal.balance
            except Exception:
                LOGGER.exception(f"Failed to fetch balance from remote bot {bot_name}")
                remote_balances[bot_name] = 0

        return CombinedBalance(total=total, local=local_balance, remote=remote_balances)

    async def get_balance(self, account_id: str, db_session: AsyncSession) -> int:
        """Get the local balance for an account."""
        stmt = select(AccountBalance.balance).where(
            AccountBalance.account_id == account_id
        )
        result = await db_session.execute(stmt)
        return result.scalar_one_or_none() or 0

    async def add_currency(
        self,
        account_id: str,
        amount: int,
        reason: str,
        db_session: AsyncSession,
        *,
        record_transaction: bool = True,
    ) -> bool:
        """Add currency to an account locally."""
        if amount <= 0:
            return False

        stmt = select(AccountBalance).where(AccountBalance.account_id == account_id)
        result = await db_session.execute(stmt)
        balance_row = result.scalar_one_or_none()

        previous_balance = 0
        if balance_row:
            previous_balance = balance_row.balance
            balance_row.balance += amount
        else:
            balance_row = AccountBalance(account_id=account_id, balance=amount)
            db_session.add(balance_row)

        if record_transaction:
            transaction = TransactionHistory(
                account_id=account_id,
                amount=amount,
                previous_balance=previous_balance,
                new_balance=previous_balance + amount,
                reason=reason,
            )
            db_session.add(transaction)

        return True

    async def remove_currency(
        self,
        account_id: str,
        amount: int,
        reason: str,
        db_session: AsyncSession,
        *,
        record_transaction: bool = True,
    ) -> bool:
        """Remove currency from an account, optionally using remote funds."""
        if amount <= 0:
            return False

        # 1. Try local first
        stmt = select(AccountBalance).where(AccountBalance.account_id == account_id)
        result = await db_session.execute(stmt)
        balance_row = result.scalar_one_or_none()

        if balance_row and balance_row.balance >= amount:
            previous_balance = balance_row.balance
            balance_row.balance -= amount

            if record_transaction:
                transaction = TransactionHistory(
                    account_id=account_id,
                    amount=-amount,
                    previous_balance=previous_balance,
                    new_balance=previous_balance - amount,
                    reason=reason,
                )
                db_session.add(transaction)

            return True

        # 2. If not enough locally and remote is enabled, try to use remote funds
        if not self.config or not self.config.remote_enabled:
            return False

        # Calculate how much more we need
        local_balance = await self.get_balance(account_id, db_session)
        remaining_to_remove = amount

        # Drain local first if it has anything
        if local_balance > 0:
            await self.set_currency(
                account_id,
                0,
                f"{reason} (Drained for larger payment)",
                db_session,
                record_transaction=record_transaction,
            )
            remaining_to_remove -= local_balance

        # Drain from remote bots
        remote_bot_service = self.global_context.get_service(RemoteBotService)
        if not remote_bot_service:
            # We already drained local, but can't reach remote.
            # This is a bit problematic, but we already committed local change if any.
            return False

        from bot.cogs.accounts.service import AccountService

        account_service = self.global_context.require_service(AccountService)
        links = await account_service.get_account_links(db_session, account_id)
        if not links:
            return False

        primary_link = next((li for li in links if li.is_primary), links[0])

        # Iterate and remove from remote bots until we have enough
        for bot_name, bot in remote_bot_service.remote_bots.items():
            if remaining_to_remove <= 0:
                break

            try:
                # First check their balance
                remote_bal = await remote_bot_service.call_remote(
                    bot,
                    "CurrencyCog",
                    "get_remote_balance",
                    RemoteBalance,
                    kwargs={
                        "platform": primary_link.platform,
                        "platform_id": primary_link.platform_id,
                    },
                )

                if remote_bal.balance > 0:
                    amount_to_take = min(remote_bal.balance, remaining_to_remove)
                    # We need a proper remote_remove_currency callable on the other end
                    # that takes platform/platform_id
                    from .cog import RemoteResult

                    remote_res = await remote_bot_service.call_remote(
                        bot,
                        "CurrencyCog",
                        "remote_remove_currency",
                        RemoteResult,
                        kwargs={
                            "platform": primary_link.platform,
                            "platform_id": primary_link.platform_id,
                            "amount": amount_to_take,
                            "reason": f"{reason} (via remote call)",
                            "record_transaction": record_transaction,
                        },
                    )
                    if remote_res.success:
                        remaining_to_remove -= amount_to_take
            except Exception:
                LOGGER.exception(f"Failed to remove currency from remote bot {bot_name}")

        return remaining_to_remove <= 0

    async def set_currency(
        self,
        account_id: str,
        amount: int,
        reason: str,
        db_session: AsyncSession,
        *,
        record_transaction: bool = True,
    ) -> None:
        """Set the local currency balance for an account."""
        stmt = select(AccountBalance).where(AccountBalance.account_id == account_id)
        result = await db_session.execute(stmt)
        balance_row = result.scalar_one_or_none()

        previous_balance = 0
        if balance_row:
            previous_balance = balance_row.balance
            balance_row.balance = amount
        else:
            balance_row = AccountBalance(account_id=account_id, balance=amount)
            db_session.add(balance_row)

        if record_transaction:
            transaction = TransactionHistory(
                account_id=account_id,
                amount=amount - previous_balance,
                previous_balance=previous_balance,
                new_balance=amount,
                reason=reason,
            )
            db_session.add(transaction)

    async def get_history_combined(
        self, account_id: str, db_session: AsyncSession, limit: int = 10
    ) -> list[CombinedHistoryItem]:
        """Get transaction history merged from all bots."""
        local_history = await self.get_history(account_id, db_session, limit)
        combined: list[CombinedHistoryItem] = [
            CombinedHistoryItem(
                amount=tx.amount,
                reason=tx.reason,
                timestamp=tx.timestamp,
                bot_name="Local",
            )
            for tx in local_history
        ]

        if not self.config or not self.config.remote_enabled:
            return combined

        remote_bot_service = self.global_context.get_service(RemoteBotService)
        if not remote_bot_service:
            return combined

        from bot.cogs.accounts.service import AccountService

        account_service = self.global_context.require_service(AccountService)
        links = await account_service.get_account_links(db_session, account_id)
        if not links:
            return combined

        primary_link = next((li for li in links if li.is_primary), links[0])

        for bot_name, bot in remote_bot_service.remote_bots.items():
            try:
                remote_hist = await remote_bot_service.call_remote(
                    bot,
                    "CurrencyCog",
                    "get_remote_history",
                    RemoteHistory,
                    kwargs={
                        "platform": primary_link.platform,
                        "platform_id": primary_link.platform_id,
                        "limit": limit,
                    },
                )
                combined.extend(
                    CombinedHistoryItem(
                        amount=item.amount,
                        reason=item.reason,
                        timestamp=datetime.datetime.fromisoformat(item.timestamp),
                        bot_name=bot_name,
                    )
                    for item in remote_hist.items
                )
            except Exception:
                LOGGER.exception(f"Failed to fetch history from remote bot {bot_name}")

        # Sort combined history by timestamp descending
        combined.sort(key=lambda x: x.timestamp, reverse=True)
        return combined[:limit]

    async def get_history(
        self, account_id: str, db_session: AsyncSession, limit: int = 10
    ) -> list[TransactionHistory]:
        """Get the local transaction history for an account."""
        stmt = (
            select(TransactionHistory)
            .where(TransactionHistory.account_id == account_id)
            .order_by(TransactionHistory.timestamp.desc())
            .limit(limit)
        )
        result = await db_session.execute(stmt)
        transactions = list(result.scalars().all())
        for t in transactions:
            db_session.expunge(t)
        return transactions

    @override
    async def on_account_merged(self, source_id: str, dest_id: str) -> None:
        """Move local balances and history from source to destination account."""
        LOGGER.info("Moving currency data from %s to %s", source_id, dest_id)

        async with get_async_session() as session:
            # 1. Get source balance
            source_bal_stmt = select(AccountBalance).where(
                AccountBalance.account_id == source_id
            )
            source_bal_res = await session.execute(source_bal_stmt)
            source_balance = source_bal_res.scalar_one_or_none()

            if source_balance:
                amount = source_balance.balance
                if amount != 0:
                    # Add to destination
                    dest_bal_stmt = select(AccountBalance).where(
                        AccountBalance.account_id == dest_id
                    )
                    dest_bal_res = await session.execute(dest_bal_stmt)
                    dest_balance = dest_bal_res.scalar_one_or_none()

                    if not dest_balance:
                        dest_balance = AccountBalance(account_id=dest_id, balance=0)
                        session.add(dest_balance)

                    dest_balance.balance += amount

                # Delete source balance record
                await session.delete(source_balance)

            # 2. Update transaction history
            __ = await session.execute(
                update(TransactionHistory)
                .where(TransactionHistory.account_id == source_id)
                .values(account_id=dest_id)
            )

            LOGGER.info("Currency data migrated for %s -> %s", source_id, dest_id)
