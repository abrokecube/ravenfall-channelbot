import asyncio
import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Explicitly import models to register them on Base metadata
from bot.cogs.accounts.models import Account as AccountModel
from bot.cogs.accounts.models import AccountLink
from bot.cogs.currency.models import AccountBalance, TransactionHistory
from bot.db import Base
from bot.db.models import User, UserCredits, UserCreditTransaction, update_schema
from bot.db.session import get_async_session

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def migrate_users(session: AsyncSession) -> None:
    """Migrate balances and transactions from UserCredits to CurrencyService.

    Args:
        session: The active database session.
    """
    credits_result = await session.execute(select(UserCredits))
    all_user_credits = list(credits_result.scalars().all())

    logger.info("Found %d user credit records to migrate.", len(all_user_credits))

    migrated_count = 0
    new_accounts_count = 0
    total_credits_migrated = 0

    for uc in all_user_credits:
        twitch_id_str = str(uc.user_id)
        credits_amount = uc.credits

        # Check if AccountLink already exists for this Twitch user
        link_result = await session.execute(
            select(AccountLink).where(
                AccountLink.platform == "twitch",
                AccountLink.platform_id == twitch_id_str,
            )
        )
        link = link_result.scalar_one_or_none()

        if link is not None:
            account_id = link.account_id
            logger.debug(
                "Found existing AccountLink for Twitch ID %s (Account ID: %s)",
                twitch_id_str,
                account_id,
            )
        else:
            # Create a new Account and AccountLink
            # Check if User details exist in users table
            user_result = await session.execute(
                select(User).where(User.twitch_id == uc.user_id)
            )
            user = user_result.scalar_one_or_none()

            username = user.name if user else twitch_id_str
            display_name = user.display_name if user else None

            account_id = str(uuid4())
            account = AccountModel(id=account_id)
            session.add(account)

            link = AccountLink(
                account_id=account_id,
                platform="twitch",
                platform_id=twitch_id_str,
                username=username,
                display_name=display_name,
                is_primary=True,
            )
            session.add(link)
            new_accounts_count += 1
            logger.info(
                "Created new Account and Link for Twitch ID %s (Account ID: %s)",
                twitch_id_str,
                account_id,
            )

        # Check if balance has already been migrated for this account.
        hist_check_result = await session.execute(
            select(TransactionHistory).where(
                TransactionHistory.account_id == account_id,
                TransactionHistory.reason.like("%Migration%"),
            )
        )
        if hist_check_result.scalars().first() is not None:
            logger.info("Account %s already migrated. Skipping.", account_id)
            continue

        # Migrate transaction history chronologically
        tx_result = await session.execute(
            select(UserCreditTransaction)
            .where(UserCreditTransaction.user_id == uc.user_id)
            .order_by(UserCreditTransaction.timestamp.asc())
        )
        old_transactions = list(tx_result.scalars().all())

        running_balance = 0
        for old_tx in old_transactions:
            prev_bal = running_balance
            new_bal = running_balance + old_tx.credits
            running_balance = new_bal

            new_tx = TransactionHistory(
                account_id=account_id,
                amount=old_tx.credits,
                previous_balance=prev_bal,
                new_balance=new_bal,
                reason=old_tx.description or "Legacy Transaction",
                timestamp=old_tx.timestamp,
            )
            session.add(new_tx)

        # Check for discrepancies between computed running balance and actual credits
        discrepancy = credits_amount - running_balance
        if discrepancy != 0:
            prev_bal = running_balance
            new_bal = credits_amount
            running_balance = new_bal

            adj_tx = TransactionHistory(
                account_id=account_id,
                amount=discrepancy,
                previous_balance=prev_bal,
                new_balance=new_bal,
                reason="Migration Balance Adjustment",
                timestamp=datetime.now(),
            )
            session.add(adj_tx)
            logger.info(
                "Discrepancy of %d credits found for account %s. Added adjustment transaction.",
                discrepancy,
                account_id,
            )

        # Set or update AccountBalance
        bal_result = await session.execute(
            select(AccountBalance).where(AccountBalance.account_id == account_id)
        )
        acc_bal = bal_result.scalar_one_or_none()

        if acc_bal is not None:
            acc_bal.balance = credits_amount
        else:
            acc_bal = AccountBalance(account_id=account_id, balance=credits_amount)
            session.add(acc_bal)

        migrated_count += 1
        total_credits_migrated += credits_amount

    logger.info("Migration Summary:")
    logger.info("  - Total UserCredits processed: %d", migrated_count)
    logger.info("  - New accounts created: %d", new_accounts_count)
    logger.info("  - Total currency migrated: %d", total_credits_migrated)


async def main() -> None:
    """Run the schema updates and migrate the database data."""
    logger.info("Running database schema updates...")
    await update_schema()

    logger.info("Starting migration process...")
    async with get_async_session() as session:
        await migrate_users(session)
    logger.info("Migration completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
