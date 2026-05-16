from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from msgspec import Struct

from bot.cogs.accounts.service import AccountService
from bot.core.components import Cog
from bot.integrations.chat_messages.deco import checks
from bot.integrations.chat_messages.enums import UserRole
from bot.integrations.commands.checks import MinPermissionLevel
from bot.integrations.commands.converters import RangeInt
from bot.integrations.commands.deco import command, parameter
from bot.integrations.commands.events import CommandEvent  # noqa: TC001
from bot.integrations.commands.exceptions import CommandError
from bot.services.remote_bot import RemoteCallableMixin, remote_callable

from .service import CurrencyService, RemoteBalance, RemoteHistory, RemoteHistoryItem


class RemoteResult(Struct):
    """Success status from a remote bot."""

    success: bool


if TYPE_CHECKING:
    from bot.core.components import EventManager

LOGGER = logging.getLogger(__name__)


class CurrencyCog(Cog, RemoteCallableMixin):
    """Cog for managing bot currency and user balances with remote bot support."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)

    @override
    async def setup(self) -> None:
        """Register CurrencyService and set up handlers."""
        service = CurrencyService()
        await self.global_context.register_service(service)
        await service.setup()
        LOGGER.info("CurrencyCog and CurrencyService started")

    # --- Remote Callables ---

    @remote_callable(RemoteBalance)
    async def get_remote_balance(self, platform: str, platform_id: str) -> RemoteBalance:
        """Fetch balance for a user identified by platform ID."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        # We need an account ID to talk to the currency service
        account = await account_service.get_or_create_account(
            platform, platform_id, f"RemoteUser_{platform_id}"
        )
        balance = await currency_service.get_balance(account.id)
        return RemoteBalance(balance=balance)

    @remote_callable(RemoteHistory)
    async def get_remote_history(
        self, platform: str, platform_id: str, limit: int = 5
    ) -> RemoteHistory:
        """Fetch transaction history for a user identified by platform ID."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        account = await account_service.get_or_create_account(
            platform, platform_id, f"RemoteUser_{platform_id}"
        )
        history = await currency_service.get_history(account.id, limit=limit)

        items = [
            RemoteHistoryItem(
                amount=tx.amount, reason=tx.reason, timestamp=tx.timestamp.isoformat()
            )
            for tx in history
        ]
        return RemoteHistory(items=items)

    @remote_callable(bool)
    async def remote_remove_currency(
        self, platform: str, platform_id: str, amount: int, reason: str
    ) -> bool:
        """Remove currency from a user identified by platform ID."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        account = await account_service.get_or_create_account(
            platform, platform_id, f"RemoteUser_{platform_id}"
        )
        return await currency_service.remove_currency(account.id, amount, reason)

    # --- Chat Commands ---

    @command(name="balance", aliases=["bal"])
    async def get_balance(self, ctx: CommandEvent) -> None:
        """Check your current balance (including remote bots)."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        account = await account_service.get_or_create_account(
            ctx.platform,
            ctx.message.author_id,
            ctx.message.author_login,
            overwrite_username=True,
        )

        combined = await currency_service.get_combined_balance(account.id)
        currency_name = currency_service.get_currency_name(combined.total)

        if not combined.remote:
            await ctx.reply(f"You currently have {combined.total} {currency_name}.")
        else:
            remote_str = ", ".join(
                f"{bot}: {bal}" for bot, bal in combined.remote.items()
            )
            await ctx.reply(
                f"You have {combined.total} {currency_name} total. "
                f"(Local: {combined.local}, Remote: {remote_str})"
            )

    @parameter("target_user", description="The user to pay")
    @parameter(
        "amount", converter=RangeInt(min_=1, max_=None), description="Amount to pay"
    )
    @command()
    async def pay(self, ctx: CommandEvent, target_user: str, amount: int) -> None:
        """Transfer currency to another user (supports remote draining)."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        sender_account = await account_service.get_or_create_account(
            ctx.platform,
            ctx.message.author_id,
            ctx.message.author_login,
            overwrite_username=True,
        )

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

        # Execute removal (which handles remote draining)
        success = await currency_service.remove_currency(
            sender_account.id, amount, f"Pay to {target_username}"
        )
        if not success:
            msg = "Insufficient funds (including remote bots)!"
            raise CommandError(msg)

        # Addition is always local to the bot where the command is run
        __ = await currency_service.add_currency(
            target_link.account_id, amount, f"Paid by {ctx.message.author_name}"
        )

        currency_name = currency_service.get_currency_name(amount)
        await ctx.reply(f"Paid {amount} {currency_name} to {target_user}!")

    @command()
    async def history(self, ctx: CommandEvent) -> None:
        """Show your last 5 transactions (merged from all bots)."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        account = await account_service.get_or_create_account(
            ctx.platform,
            ctx.message.author_id,
            ctx.message.author_login,
            overwrite_username=True,
        )
        transactions = await currency_service.get_history_combined(account.id, limit=5)

        if not transactions:
            await ctx.reply("You have no transaction history yet.")
            return

        lines = ["Your recent transactions (All Bots):"]
        for tx in transactions:
            currency_name = currency_service.get_currency_name(tx.amount)
            sign = "+" if tx.amount > 0 else ""
            bot_tag = f"[{tx.bot_name}] " if tx.bot_name != "Local" else ""
            lines.append(f"{bot_tag}{sign}{tx.amount} {currency_name} - {tx.reason}")

        await ctx.reply("\n".join(lines))

    # --- Admin Commands (Local Only) ---
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
        """Add currency to a user's account locally."""
        account_service = self.global_context.require_service(AccountService)
        currency_service = self.global_context.require_service(CurrencyService)

        target_username = target_user.lstrip("@").lower()
        target_link = await account_service.find_link_by_username(
            ctx.platform, target_username
        )

        if not target_link:
            msg = f"User '{target_user}' not found on {ctx.platform}."
            raise CommandError(msg)

        __ = await currency_service.add_currency(
            target_link.account_id, amount, f"Admin: {reason}"
        )
        c_name = currency_service.get_currency_name(amount)
        await ctx.reply(f"Gave {amount} {c_name} to {target_user} locally.")

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
        """Remove currency from a user's account locally."""
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
            await ctx.reply(f"Took {amount} {c_name} from {target_user} locally.")
        else:
            await ctx.reply(f"{target_user} has insufficient funds locally.")

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
        """Set a user's local currency balance."""
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
        await ctx.reply(f"Set {target_user}'s local balance to {amount} {c_name}.")
