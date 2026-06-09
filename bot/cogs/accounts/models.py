from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import (
    Mapped,
    Relationship,
    mapped_column,
    relationship,
)

from bot.db import Base


class Account(Base):
    """Central entity representing a user across all platforms."""

    __tablename__: str = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID string

    links: Relationship[list[AccountLink]] = relationship(
        "AccountLink", back_populates="account", cascade="all, delete-orphan"
    )


class AccountLink(Base):
    """Link between an Account and a specific platform identifier."""

    __tablename__: str = "account_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"))
    platform: Mapped[str] = mapped_column(String)
    platform_id: Mapped[str] = mapped_column(String)
    username: Mapped[str] = mapped_column(String)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    account: Relationship[Account] = relationship("Account", back_populates="links")

    __table_args__: object = (
        UniqueConstraint("platform", "platform_id", name="uq_platform_id"),
    )
