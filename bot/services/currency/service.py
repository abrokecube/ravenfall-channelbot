from __future__ import annotations

import logging

from sqlalchemy import select

from bot.core.components import BaseService
from bot.db.session import get_async_session

from .models import AccountBalance, TransactionHistory

LOGGER = logging.getLogger(__name__)


class CurrencyService(BaseService):
    """Service for managing user currency balances and transactions."""

    async def get_balance(self, account_id: str) -> int:
        """Get the current balance for an account.

        Args:
            account_id: The global account ID.

        Returns:
            The current balance (defaults to 0 if no balance record exists).
        """
        async with get_async_session() as session:
            stmt = select(AccountBalance.balance).where(
                AccountBalance.account_id == account_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() or 0

    async def add_currency(self, account_id: str, amount: int, reason: str) -> bool:
        """Add currency to an account.

        Args:
            account_id: The global account ID.
            amount: The amount to add (must be positive).
            reason: The reason for the transaction.

        Returns:
            True if successful, False otherwise.
        """
        if amount <= 0:
            return False

        async with get_async_session() as session:
            # 1. Get or create balance
            stmt = select(AccountBalance).where(AccountBalance.account_id == account_id)
            result = await session.execute(stmt)
            balance_row = result.scalar_one_or_none()

            previous_balance = 0
            if balance_row:
                previous_balance = balance_row.balance
                balance_row.balance += amount
            else:
                balance_row = AccountBalance(account_id=account_id, balance=amount)
                session.add(balance_row)

            # 2. Record transaction
            transaction = TransactionHistory(
                account_id=account_id,
                amount=amount,
                previous_balance=previous_balance,
                new_balance=previous_balance + amount,
                reason=reason,
            )
            session.add(transaction)

            await session.commit()
            return True

    async def remove_currency(self, account_id: str, amount: int, reason: str) -> bool:
        """Remove currency from an account.

        Args:
            account_id: The global account ID.
            amount: The amount to remove (must be positive).
            reason: The reason for the transaction.

        Returns:
            True if successful, False if insufficient funds or other error.
        """
        if amount <= 0:
            return False

        async with get_async_session() as session:
            stmt = select(AccountBalance).where(AccountBalance.account_id == account_id)
            result = await session.execute(stmt)
            balance_row = result.scalar_one_or_none()

            if not balance_row or balance_row.balance < amount:
                return False

            previous_balance = balance_row.balance
            balance_row.balance -= amount

            # Record transaction
            transaction = TransactionHistory(
                account_id=account_id,
                amount=-amount,
                previous_balance=previous_balance,
                new_balance=previous_balance - amount,
                reason=reason,
            )
            session.add(transaction)

            await session.commit()
            return True

    async def set_currency(self, account_id: str, amount: int, reason: str) -> None:
        """Directly set the currency balance for an account.

        Args:
            account_id: The global account ID.
            amount: The new balance amount.
            reason: The reason for the change.
        """
        async with get_async_session() as session:
            stmt = select(AccountBalance).where(AccountBalance.account_id == account_id)
            result = await session.execute(stmt)
            balance_row = result.scalar_one_or_none()

            previous_balance = 0
            if balance_row:
                previous_balance = balance_row.balance
                balance_row.balance = amount
            else:
                balance_row = AccountBalance(account_id=account_id, balance=amount)
                session.add(balance_row)

            # Record transaction
            transaction = TransactionHistory(
                account_id=account_id,
                amount=amount - previous_balance,
                previous_balance=previous_balance,
                new_balance=amount,
                reason=reason,
            )
            session.add(transaction)

            await session.commit()

    async def get_history(
        self, account_id: str, limit: int = 10
    ) -> list[TransactionHistory]:
        """Get the transaction history for an account.

        Args:
            account_id: The global account ID.
            limit: The maximum number of transactions to return.

        Returns:
            A list of TransactionHistory objects.
        """
        async with get_async_session() as session:
            stmt = (
                select(TransactionHistory)
                .where(TransactionHistory.account_id == account_id)
                .order_by(TransactionHistory.timestamp.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            transactions = list(result.scalars().all())
            for t in transactions:
                session.expunge(t)
            return transactions
