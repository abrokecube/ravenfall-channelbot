from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bot.core.components import Cog
from bot.integrations.chat_messages.deco import checks
from bot.integrations.chat_messages.enums import UserRole
from bot.integrations.commands.checks import MinPermissionLevel
from bot.integrations.commands.converters import RangeInt
from bot.integrations.commands.deco import command, parameter
from bot.integrations.commands.exceptions import CommandError
from bot.services.accounts.service import AccountService
from bot.services.currency.service import CurrencyService

if TYPE_CHECKING:
    from bot.core.components import EventManager
    from bot.integrations.commands.events import CommandEvent

LOGGER = logging.getLogger(__name__)


class CurrencyCog(Cog):
    """Cog for managing bot currency and user balances."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)

    @command(name="balance", aliases=["bal"])
    async def get_balance(self, ctx: CommandEvent) -> None:
        """Check your current balance."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        # Get or create account for the sender
        account = await account_service.get_or_create_account(
            ctx.platform, ctx.message.author_id, ctx.message.author_name
        )

        balance = await currency_service.get_balance(account.id)
        currency_name = currency_service.get_currency_name(balance)

        await ctx.reply(f"You currently have {balance} {currency_name}.")

    @parameter("target_user", description="The user to pay")
    @parameter(
        "amount", converter=RangeInt(min_=1, max_=None), description="Amount to pay"
    )
    @command()
    async def pay(self, ctx: CommandEvent, target_user: str, amount: int) -> None:
        """Transfer currency to another user."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        # 1. Resolve sender
        sender_account = await account_service.get_or_create_account(
            ctx.platform, ctx.message.author_id, ctx.message.author_name
        )

        # 2. Resolve target (on the same platform for now)
        target_username = target_user.lstrip("@").lower()
        target_link = await account_service.find_link_by_username(
            ctx.platform, target_username
        )

        if not target_link:
            msg = f"User '{target_user}' has not used the bot on {ctx.platform} yet."
            raise CommandError(msg)

        if target_link.account_id == sender_account.id:
            msg = "You cannot pay yourself!"
            raise CommandError(msg)

        # 3. Execute transfer
        success = await currency_service.remove_currency(
            sender_account.id, amount, f"Pay to {target_username}"
        )
        if not success:
            msg = "Insufficient funds!"
            raise CommandError(msg)

        __ = await currency_service.add_currency(
            target_link.account_id, amount, f"Paid by {ctx.message.author_name}"
        )

        currency_name = currency_service.get_currency_name(amount)
        await ctx.reply(f"Paid {amount} {currency_name} to {target_user}!")

    @command()
    async def history(self, ctx: CommandEvent) -> None:
        """Show your last 5 transactions."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        account = await account_service.get_or_create_account(
            ctx.platform, ctx.message.author_id, ctx.message.author_name
        )
        transactions = await currency_service.get_history(account.id, limit=5)

        if not transactions:
            await ctx.reply("You have no transaction history yet.")
            return

        lines = ["Your recent transactions:"]
        for tx in transactions:
            currency_name = currency_service.get_currency_name(tx.amount)
            sign = "+" if tx.amount > 0 else ""
            lines.append(f"{sign}{tx.amount} {currency_name} - {tx.reason}")

        await ctx.reply("\n".join(lines))

    # Admin commands
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @parameter("target_user", description="The target user")
    @parameter(
        "amount", converter=RangeInt(min_=1, max_=None), description="Amount to give"
    )
    @parameter(
        "reason", greedy=True, default="Admin gift", description="Reason for giving"
    )
    @command()
    async def givecurrency(
        self, ctx: CommandEvent, target_user: str, amount: int, reason: str
    ) -> None:
        """Add currency to a user's account."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        target_username = target_user.lstrip("@").lower()
        target_link = await account_service.find_link_by_username(
            ctx.platform, target_username
        )

        if not target_link:
            msg = f"User '{target_user}' not found on {ctx.platform}."
            raise CommandError(msg)

        success = await currency_service.add_currency(
            target_link.account_id, amount, f"Admin: {reason}"
        )
        if success:
            c_name = currency_service.get_currency_name(amount)
            await ctx.reply(f"Gave {amount} {c_name} to {target_user}.")
        else:
            await ctx.reply("Failed to add currency.")

    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @parameter("target_user", description="The target user")
    @parameter(
        "amount", converter=RangeInt(min_=1, max_=None), description="Amount to take"
    )
    @parameter(
        "reason", greedy=True, default="Admin removal", description="Reason for taking"
    )
    @command()
    async def takecurrency(
        self, ctx: CommandEvent, target_user: str, amount: int, reason: str
    ) -> None:
        """Remove currency from a user's account."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        target_username = target_user.lstrip("@").lower()
        target_link = await account_service.find_link_by_username(
            ctx.platform, target_username
        )

        if not target_link:
            msg = f"User '{target_user}' not found on {ctx.platform}."
            raise CommandError(msg)

        success = await currency_service.remove_currency(
            target_link.account_id, amount, f"Admin: {reason}"
        )
        if success:
            c_name = currency_service.get_currency_name(amount)
            await ctx.reply(f"Took {amount} {c_name} from {target_user}.")
        else:
            await ctx.reply(f"{target_user} has insufficient funds.")

    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @parameter("target_user", description="The target user")
    @parameter("amount", converter=RangeInt(min_=0, max_=None), description="New balance")
    @parameter(
        "reason", greedy=True, default="Admin set", description="Reason for setting"
    )
    @command()
    async def setcurrency(
        self, ctx: CommandEvent, target_user: str, amount: int, reason: str
    ) -> None:
        """Set a user's currency balance."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        target_username = target_user.lstrip("@").lower()
        target_link = await account_service.find_link_by_username(
            ctx.platform, target_username
        )

        if not target_link:
            msg = f"User '{target_user}' not found on {ctx.platform}."
            raise CommandError(msg)

        await currency_service.set_currency(
            target_link.account_id, amount, f"Admin: {reason}"
        )
        c_name = currency_service.get_currency_name(amount)
        await ctx.reply(f"Set {target_user}'s balance to {amount} {c_name}.")
