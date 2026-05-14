from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column  # noqa: TC002

from bot.db import Base


class AccountBalance(Base):
    """Current currency balance for an Account."""

    __tablename__: str = "account_balances"

    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))


class TransactionHistory(Base):
    """History of currency transactions for auditing."""

    __tablename__: str = "currency_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), index=True)
    amount: Mapped[int] = mapped_column(
        BigInteger
    )  # Positive for add, negative for remove
    previous_balance: Mapped[int] = mapped_column(BigInteger)
    new_balance: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.now, server_default=text("CURRENT_TIMESTAMP")
    )
