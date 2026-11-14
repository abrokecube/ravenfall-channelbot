"""!exchange: trade crafting ingredients for a crafted item from stock, or the other way round.

The user gifts their side by hand to any of the owner's stock accounts in the
town, and the bot watches the game's gift replies. Once everything has arrived
the bot gifts its side from existing stock. It never crafts.

Sessions run on the main event loop, where game messages arrive, whichever loop
the command came in on. Items owed to a user who has left are saved in
pending_exchange_deliveries and gifted when they rejoin. Every step is logged
to the bot.exchange logger and saved per session (see bot/exchange_log.py).
"""

from __future__ import annotations

import asyncio
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select

from ravenpy import ravenpy
from ravenpy.ravenpy import Item

from bot.config import get_settings
from bot.ravenfallchannel import RFChannel
from bot.exchange_log import (
    SessionLogAdapter, exchange_logger, flush_exchange_logs, install_db_log_handler,
    new_session_id, purge_old_exchanges,
)
from bot.models import RFChannelSubEvent
from bot.multichat_command import get_char_items
from bot.ravenfallloc import pl
from database.models import ExchangeLogEntry, ExchangeSession, PendingExchangeDelivery, SenderData
from database.session import get_async_session
from database.utils import add_credits, get_user_credits
from utils.routines import routine
from utils.utils import upload_to_bin

from ..commands.checks import MinPermissionLevel, TwitchOnly
from ..commands.cog import Cog
from ..commands.converters import RangeInt, RFChannelConverter, RFItemConverter, TwitchUsername
from ..commands.decorators import checks, command, parameter
from ..commands.enums import UserRole
from ..commands.events import CommandEvent
from . import redeem_rf
from .redeem_rf import (
    CouldNotSendItemsError, CouldNotSendMessageError, ItemNotFoundError, OutOfItemsError,
    PartialSendError, RecipientNotFoundError, UnconfirmedSendError, query_player_item_count,
    wait_while_dungeon_prepares,
)

FEE = 10
GIFT_WINDOW = 120.0  # seconds of unpaused time to send the gifts
MAX_SESSION = 30 * 60.0  # overall limit, pauses included
POLL_INTERVAL = 5.0
DELIVERY_INTERVAL = 15.0
LOG_FLUSH_INTERVAL = 5.0
LOG_LOOKBACK = timedelta(days=60)

ITEM_REGEX = r"^([a-zA-Z ]|2[hH])+$"


# ---------------------------------------------------------------- helpers

class StockUnavailableError(Exception):
    pass

# Sessions in these states when the bot stopped had items in flight
OPEN_STATUSES = ("checking", "waiting", "paused", "sending")

def recipe_of(item: Item) -> Dict[Item, int]:
    return {ing.item: ing.amount for ing in item.craft_ingredients}


def exchange_problem(item: Item) -> Optional[str]:
    """Why `item` can't be exchanged, or None if it can."""
    if not item.craft_ingredients:
        return f"{item.name} cannot be exchanged because it is not crafted."
    if item.soulbound:
        return f"{item.name} cannot be exchanged because it is soulbound."
    for ing in item.craft_ingredients:
        if ing.item.soulbound:
            return f"{item.name} cannot be exchanged because {ing.item.name} is soulbound."
        if ing.amount <= 0:
            return f"{item.name} cannot be exchanged."
    return None


def sides(item: Item, count: int, direction: str) -> tuple[Dict[Item, int], Dict[Item, int]]:
    """(what the user gives, what the bot gives)."""
    ingredients = {ing: n * count for ing, n in recipe_of(item).items()}
    crafted = {item: count}
    if direction == "craft":
        return ingredients, crafted
    return crafted, ingredients


def fmt_items(items: Dict[Item, int] | Dict[str, int]) -> str:
    parts = []
    for it, n in items.items():
        if n <= 0:
            continue
        name = it.name if isinstance(it, Item) else it
        parts.append(f"{n:,}× {name}{pl(n, '', '(s)')}")
    if not parts:
        return "nothing"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def by_name(items: Dict[Item, int]) -> Dict[str, int]:
    return {it.name: n for it, n in items.items() if n}


def owner_ping() -> str:
    return f"pinging @{get_settings().owner_twitch_username}"


async def get_sendable_stock(channel, exclude_login: str = "") -> Dict[str, int]:
    """Item ID -> amount the bot can gift, counted the way send_items picks stacks.

    Raises:
        StockUnavailableError: If multichat didn't answer.
    """
    char_items = await get_char_items(channel.channel_id)
    if char_items.get("status") != 200 or "data" not in char_items:
        raise StockUnavailableError(char_items.get("error") or f"status {char_items.get('status')}")
    stock: Counter = Counter()
    for user in char_items.get("data") or []:
        if user["user_name"].lower() == exclude_login.lower():
            continue
        seen = set()
        for user_item in user["items"]:
            if user_item["soulbound"] or user_item["equipped"]:
                continue
            # send_items only uses the first stack of an item on each character
            if user_item["id"] in seen:
                continue
            seen.add(user_item["id"])
            stock[user_item["id"]] += user_item["amount"]
    return dict(stock)


async def get_stock_account_names(channel, exclude_login: str = "") -> Dict[str, set[str]]:
    """Stock account login -> lowercase names it can appear under (login and display name)."""
    char_items = await get_char_items(channel.channel_id)
    if char_items.get("status") != 200 or "data" not in char_items:
        raise StockUnavailableError(char_items.get("error") or f"status {char_items.get('status')}")
    logins = {
        user["user_name"].lower()
        for user in char_items.get("data") or []
        if user.get("user_name") and user["user_name"].lower() != exclude_login.lower()
    }
    accounts = {login: {login} for login in logins}
    if not logins:
        return accounts
    async with get_async_session() as session:
        rows = await session.execute(
            select(SenderData.username, SenderData.display_name).where(
                SenderData.channel_platform_id == str(channel.channel_id),
            )
        )
        for username, display_name in rows.all():
            if username and username.lower() in accounts and display_name:
                accounts[username.lower()].add(display_name.lower())
    return accounts


async def load_rows(stmt) -> list:
    """Run a select and return its objects detached, so they stay readable after the session closes.

    get_async_session() commits on exit, which would otherwise expire them.
    """
    async with get_async_session() as session:
        rows = list((await session.execute(stmt)).scalars())
        session.expunge_all()
    return rows


async def player_names(channel) -> Optional[set[str]]:
    players = await channel.get_players()
    if players is None:
        return None
    return {p["name"].lower() for p in players if p.get("name")}


def message_key(channel, message) -> Optional[str]:
    try:
        match = channel.rfloc.identify_string(message["Format"])
    except Exception:
        return None
    return match.key if match is not None else None


@dataclass
class GiveResult:
    delivered: int = 0
    pending: int = 0  # saved for when the user is back
    unconfirmed: int = 0  # unknown and left alone, owner pinged
    failed: int = 0  # couldn't be sent: out of stock or an error


# ---------------------------------------------------------------- one session

class ExchangeRun:
    """One !exchange craft/uncraft from the instructions to the last gift."""

    def __init__(self, cog: "ExchangeCog", channel, ctx_message, item: Item, count: int, direction: str):
        self.cog = cog
        self.channel = channel
        self.id = new_session_id()
        self.user_login: str = ctx_message.author_login.lower()
        self.user_display: str = (ctx_message.author_name or ctx_message.author_login).lower()
        self.user_id: str = str(ctx_message.author_id)
        self.reply_id: Optional[str] = getattr(ctx_message, "id", None)
        self.item = item
        self.count = count
        self.direction = direction
        self.give, self.get = sides(item, count, direction)
        self.give_by_name = {it.name.lower(): it for it in self.give}
        self.received: Counter = Counter()  # Item -> count
        self.sent: Counter = Counter()  # item name -> count, everything gifted to the user
        self.returned: Counter = Counter()
        self.pending: Counter = Counter()
        self.baseline: Dict[Item, int] = {}
        self.stock_before: Dict[str, int] = {}
        self.status = "checking"
        self.suggested: Optional[str] = None
        self.accounts: Dict[str, set[str]] = {}
        self.account_names_seen: set[str] = set()
        self.present_accounts: List[str] = []
        self.user_present = True
        self.paused_seconds = 0.0
        self.fee_trans_id: Optional[int] = None
        self.created_at = datetime.now()
        self.ended_at: Optional[datetime] = None
        self.outcome: Optional[str] = None
        self.log = SessionLogAdapter(self.id, self.user_login, channel.channel_name)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.queue: Optional[asyncio.Queue] = None
        self.cancel_requested = False
        self._force_poll = False

    # ---- plumbing

    def missing(self) -> Dict[Item, int]:
        return {it: n - self.received[it] for it, n in self.give.items() if self.received[it] < n}

    def complete(self) -> bool:
        return not self.missing()

    def request_cancel(self) -> None:
        """Safe to call from any thread or loop."""
        self.cancel_requested = True
        if self.loop is not None and self.queue is not None:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, None)

    def _on_game_message(self, message) -> None:
        if self.loop is None or self.queue is None:
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, message)
        except RuntimeError:
            pass  # the loop has closed

    async def say(self, text: str, reply: bool = True) -> None:
        if not reply or not self.reply_id:
            text = f"@{self.user_login} {text}"
        try:
            await self.channel.send_chat_message(text, reply_id=self.reply_id if reply else None)
        except Exception:
            self.log.warning(f"Could not send chat message: {text!r}", exc_info=True)
            return
        self.log.debug(f"Said: {text}")

    async def save(self, **extra) -> None:
        row = dict(
            id=self.id,
            twitch_user_id=self.user_id,
            user_login=self.user_login,
            channel_id=str(self.channel.channel_id),
            channel_name=self.channel.channel_name,
            direction=self.direction,
            item_id=self.item.id,
            item_name=self.item.name,
            count=self.count,
            status=self.status,
            required=by_name(self.give),
            received=by_name(self.received),
            sent=dict(self.sent),
            returned=dict(self.returned),
            pending=dict(self.pending),
            baseline=by_name(self.baseline),
            suggested_account=self.suggested,
            fee_trans_id=self.fee_trans_id,
            paused_seconds=round(self.paused_seconds, 1),
            created_at=self.created_at,
            updated_at=datetime.now(),
            ended_at=self.ended_at,
        )
        row.update(extra)
        try:
            async with get_async_session() as session:
                await session.merge(ExchangeSession(**row))
        except Exception:
            self.log.error("Could not save the session", exc_info=True)

    async def set_status(self, status: str) -> None:
        if status != self.status:
            self.log.info(f"Status {self.status} -> {status}")
        self.status = status
        await self.save()

    # ---- main

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.channel.ravenfall_message_listeners.append(self._on_game_message)
        try:
            await self._run()
        except Exception:
            self.log.error("The exchange failed unexpectedly", exc_info=True)
            self.outcome = self.outcome or "failed"
            self.status = "failed"
            await self.say(f"❌ An unknown error occurred in your exchange. {owner_ping()} (Exchange {self.id})")
        finally:
            try:
                self.channel.ravenfall_message_listeners.remove(self._on_game_message)
            except ValueError:
                pass
            self.cog.active.pop(self.user_id, None)
            self.ended_at = datetime.now()
            await self.save()
            self.log.info(
                f"summary: {self.direction} {self.item.name} x{self.count} user={self.user_login} "
                f"result={self.outcome or self.status} received={by_name(self.received)} sent={dict(self.sent)} "
                f"returned={dict(self.returned)} pending={dict(self.pending)} fee_trans={self.fee_trans_id} "
                f"duration={(self.ended_at - self.created_at).total_seconds():.0f}s paused={self.paused_seconds:.0f}s"
            )
            await flush_exchange_logs()

    async def _reject(self, text: str, reason: str) -> None:
        self.log.info(f"Rejected: {reason}")
        self.outcome = "rejected"
        await self.set_status("rejected")
        await self.say(text)

    async def _run(self) -> None:
        log = self.log
        log.info(
            f"Start: {self.direction} {self.item.name} x{self.count} in {self.channel.channel_name}; "
            f"user gives {by_name(self.give)}, bot gives {by_name(self.get)}"
        )
        await self.save()

        # Stock
        try:
            stock = await get_sendable_stock(self.channel, self.user_login)
        except StockUnavailableError as e:
            await self._reject("❌ Error: Could not read the stock. Please try again.", f"stock unavailable: {e}")
            return
        self.stock_before = stock
        log.info("Stock: " + ", ".join(f"{it.name}={stock.get(it.id, 0)}" for it in list(self.get) + list(self.give)))
        short = {it: n for it, n in self.get.items() if stock.get(it.id, 0) < n}
        if short:
            await self._reject(
                f"There are not enough items in stock for this exchange: "
                + ", ".join(f"{stock.get(it.id, 0):,}/{n:,}× {it.name}" for it, n in short.items()) + ".",
                f"short of stock {by_name(short)}",
            )
            return

        # Credits
        async with get_async_session() as session:
            balance = await get_user_credits(session, self.user_id)
        log.info(f"Credit balance: {balance}")
        if balance < FEE:
            await self._reject(
                f"You need {FEE} item credits for an exchange. You have {balance:,} {pl(balance, 'credit', 'credits')}.",
                f"balance {balance} < fee {FEE}",
            )
            return

        # The user's inventory, which is also the baseline for later checks
        try:
            await wait_while_dungeon_prepares(
                self.channel,
                on_wait=lambda: self.say("The game is preparing a dungeon. Your exchange will start once it's ready."),
            )
        except redeem_rf.TimeoutError:
            await self._reject(
                "The game is still preparing a dungeon. Please try again later.", "dungeon preparation took too long"
            )
            return
        names = await player_names(self.channel)
        if names is not None and not ({self.user_login, self.user_display} & names):
            await self._reject("❌ Error: You are not in the game.", "not in the town's player list")
            return
        for it, n in self.give.items():
            try:
                have = await query_player_item_count(self.channel, self.user_login, it, log=log)
            except RecipientNotFoundError as e:
                await self._reject("❌ Error: You are not in the game.", str(e))
                return
            if have is None:
                await self._reject(
                    "❌ Error: Could not check your inventory. Please try again.", f"no answer to !count {it.name}"
                )
                return
            self.baseline[it] = have
        log.info(f"Inventory baseline: {by_name(self.baseline)}")
        lacking = {it: n for it, n in self.give.items() if self.baseline[it] < n}
        if lacking:
            await self._reject(
                "You don't have enough items: "
                + ", ".join(f"{self.baseline[it]:,}/{n:,}× {it.name}" for it, n in lacking.items()) + ".",
                f"user is short {by_name(lacking)}",
            )
            return

        # Where to gift
        await self._refresh_accounts(names)
        if not self.present_accounts:
            await self._reject("❌ Error: No exchange account is in this town.", "no stock account in town")
            return
        self.suggested = self.cog.pick_account(self.present_accounts)
        log.info(f"Stock accounts: {sorted(self.accounts)}; in town: {self.present_accounts}; suggested: {self.suggested}")

        await self.set_status("waiting")
        await self.say(
            f"Gift {fmt_items(self.give)} to {self.suggested} within {int(GIFT_WINDOW // 60)} minutes "
            f"to get {fmt_items(self.get)}. Fee: {FEE} credits. Type !exchange cancel to stop. (Exchange {self.id})"
        )

        await self._collect()
        log.info(f"Collection ended: {self.outcome}; received {by_name(self.received)}")

        if self.outcome == "complete":
            await self._finish()
        else:
            await self._abort()

    # ---- presence

    async def _refresh_accounts(self, names: Optional[set[str]]) -> None:
        try:
            accounts = await get_stock_account_names(self.channel, self.user_login)
        except Exception:
            self.log.warning("Could not list stock accounts", exc_info=True)
            accounts = None
        if accounts:
            self.accounts = accounts
            for ns in accounts.values():
                self.account_names_seen |= ns
        if names is None:
            return
        present = sorted(login for login, ns in self.accounts.items() if ns & names)
        if present != self.present_accounts:
            self.log.info(f"Stock accounts in town: {self.present_accounts} -> {present}")
        self.present_accounts = present

    async def _poll(self) -> None:
        names = await player_names(self.channel)
        if names is None:
            self.log.debug("Player list query failed")
            return
        preparing = self.channel.sub_event == RFChannelSubEvent.DUNGEON_PREPARE
        here = bool({self.user_login, self.user_display} & names)
        if here and not self.user_present:
            self.log.info("User is in the player list again")
            self.user_present = True
        elif not here and self.user_present and not preparing:
            # The player list can change during dungeon preparation, so absence only counts after it
            self.log.info("User is no longer in the player list")
            self.user_present = False
        before = list(self.present_accounts)
        await self._refresh_accounts(None if (preparing and not names) else names)
        if preparing and not self.present_accounts and before:
            self.present_accounts = before

    def _pause_reasons(self) -> List[str]:
        reasons = []
        if not self.user_present:
            reasons.append("user away")
        if not self.present_accounts:
            reasons.append("no stock account in town")
        if self.channel.sub_event == RFChannelSubEvent.DUNGEON_PREPARE:
            reasons.append("dungeon being prepared")
        return reasons

    # ---- collecting

    def _handle_message(self, message) -> bool:
        """Apply a game message. Returns whether a gift was counted."""
        key = message_key(self.channel, message)
        if key is None:
            return False
        recipient = message.get("Recipent") or {}
        from_name = (recipient.get("PlatformUserName") or "").lower()
        from_user = from_name in (self.user_login, self.user_display)
        args = message.get("Args") or []

        if key == "gift" and from_user:
            try:
                amount = int(args[0])
                item_name = str(args[1])
                to = str(args[2]).lower()
            except (IndexError, TypeError, ValueError):
                self.log.warning(f"Unreadable gift reply: {message.get('Format')!r} {args!r}")
                return False
            if to not in self.account_names_seen:
                self.log.debug(f"Ignored gift of {amount}x {item_name} to {to}: not a stock account")
                return False
            it = self.give_by_name.get(item_name.lower())
            if it is None:
                self.log.debug(f"Ignored gift of {amount}x {item_name} to {to}: not part of this exchange")
                return False
            before = self.received[it]
            self.received[it] += amount
            extra = max(0, self.received[it] - self.give[it]) - max(0, before - self.give[it])
            self.log.info(
                f"Counted gift of {amount}x {it.name} to {to}: {self.received[it]}/{self.give[it]}"
                + (f" ({extra} more than needed)" if extra else "")
            )
            return True

        if key == "leave" and from_user:
            self.log.info("User left the game (leave reply)")
            self.user_present = False
        elif key == "kick_player" and args and str(args[0]).lower() in (self.user_login, self.user_display):
            self.log.info("User was kicked from the game")
            self.user_present = False
        elif key == "kick_players":
            self.log.info(f"Players were kicked: {args!r}")
            self._force_poll = True
        elif key == "leave" and from_name in self.account_names_seen:
            self.log.info(f"Stock account {from_name} left the game")
            self._force_poll = True
        return False

    async def _collect(self) -> None:
        loop = asyncio.get_running_loop()
        window_left = GIFT_WINDOW
        hard_deadline = loop.time() + MAX_SESSION
        last = loop.time()
        next_poll = last + POLL_INTERVAL
        self._force_poll = False
        was_paused: List[str] = []
        was_user_present = True
        last_suggested = self.suggested

        while True:
            now = loop.time()
            reasons = self._pause_reasons()
            if reasons:
                self.paused_seconds += now - last
            else:
                window_left -= now - last
            last = now

            if self.cancel_requested:
                self.outcome = "cancelled"
                return
            if self.complete():
                self.outcome = "complete"
                return
            if window_left <= 0:
                self.outcome = "timed_out"
                return
            if now >= hard_deadline:
                self.outcome = "expired"
                return

            # Announce changes
            if reasons != was_paused:
                self.log.info(
                    f"{'Paused' if reasons else 'Resumed'} ({', '.join(reasons) or was_paused}); "
                    f"{window_left:.0f}s of the window and {hard_deadline - now:.0f}s overall left"
                )
                await self.set_status("paused" if reasons else "waiting")
            if was_user_present and not self.user_present:
                await self.say("You left the game. Your exchange is paused until you rejoin.", reply=False)
            elif not was_user_present and self.user_present:
                m, s = divmod(max(0, int(window_left)), 60)
                await self.say(f"Welcome back! Your exchange has resumed ({m}:{s:02d} left).", reply=False)
            was_user_present = self.user_present
            if self.present_accounts and self.suggested not in self.present_accounts:
                old = self.suggested
                self.suggested = self.cog.pick_account(self.present_accounts)
                self.log.info(f"Suggested account {old} left; now {self.suggested}")
                if self.suggested != last_suggested:
                    await self.say(f"{old} left the game. Send the rest ({fmt_items(self.missing())}) to {self.suggested} instead.")
                    last_suggested = self.suggested
                await self.save()
            elif not self.present_accounts and "no stock account in town" not in was_paused:
                await self.say("No exchange account is in the game right now. Your exchange is paused.")
            was_paused = reasons

            if now >= next_poll or self._force_poll:
                self._force_poll = False
                try:
                    await self._poll()
                except Exception:
                    self.log.warning("Presence poll failed", exc_info=True)
                next_poll = loop.time() + POLL_INTERVAL
                continue

            wait = min(next_poll - now, hard_deadline - now)
            if not reasons:
                wait = min(wait, window_left)
            try:
                message = await asyncio.wait_for(self.queue.get(), timeout=max(0.01, wait))
            except asyncio.TimeoutError:
                continue
            if message is None:
                continue
            counted = False
            try:
                counted = self._handle_message(message)
            except Exception:
                self.log.warning("Could not handle a game message", exc_info=True)
            if counted:
                await self.save()
                if not self.complete():
                    got = ", ".join(f"{self.received[it]:,}/{n:,}× {it.name}" for it, n in self.give.items())
                    await self.say(f"Received {got}. Still need {fmt_items(self.missing())}.")

    # ---- giving

    async def give_items(self, it: Item, n: int, kind: str) -> GiveResult:
        """Gift `n` of `it` to the user. Whatever can't be gifted because they left is saved for later."""
        result = GiveResult()
        if n <= 0:
            return result
        if not self.user_present:
            self.log.info(f"User is away: saving {n}x {it.name} ({kind}) for later")
            await self._add_pending(it, n, kind, "user away")
            result.pending = n
            return result
        self.log.info(f"Gifting {n}x {it.name} ({kind})")
        try:
            await redeem_rf.send_items(
                self.user_login, self.channel, it.name, n,
                on_busy=lambda: self.say("The game is preparing a dungeon. Your items will be sent once it's ready."),
                verify_recipient=True, log=self.log,
            )
            result.delivered = n
        except PartialSendError as e:
            result.delivered = e.items_sent
            result.failed = n - e.items_sent
            self.log.warning(f"Partial send of {it.name}: {e.items_sent}/{n}")
        except OutOfItemsError:
            result.failed = n
            self.log.warning(f"Out of stock of {it.name}")
        except RecipientNotFoundError as e:
            result.delivered = e.items_sent
            result.pending = n - e.items_sent
            self.user_present = False
            self.log.info(f"User is not in the game: saving {result.pending}x {it.name} for later")
            await self._add_pending(it, result.pending, kind, "user left")
        except UnconfirmedSendError as e:
            result.delivered = e.items_sent
            rest = n - e.items_sent - e.unconfirmed
            if e.recipient_absent:
                self.user_present = False
                result.pending = e.unconfirmed + rest
                await self._add_pending(it, e.unconfirmed, kind, "unconfirmed gift, user left",
                                        unconfirmed=True, baseline=e.baseline)
                if rest > 0:
                    await self._add_pending(it, rest, kind, "user left")
            else:
                result.unconfirmed = e.unconfirmed
                result.failed = rest
                await self.say(
                    f"❌ Could not confirm {e.unconfirmed:,}× {it.name} sent to @{self.user_login}. "
                    f"{owner_ping()} (Exchange {self.id})"
                )
        except (CouldNotSendMessageError, CouldNotSendItemsError, redeem_rf.TimeoutError, ItemNotFoundError) as e:
            result.delivered = e.items_sent
            result.failed = n - e.items_sent
            self.log.warning(f"Could not gift {it.name}: {e!r}")
        except Exception as e:
            result.delivered = getattr(e, "items_sent", 0)
            result.failed = n - result.delivered
            self.log.error(f"Could not gift {it.name}", exc_info=True)
        if result.delivered:
            if kind == "output":
                self.sent[it.name] += result.delivered
            else:
                self.returned[it.name] += result.delivered
        await self.save()
        return result

    async def _add_pending(self, it: Item, n: int, kind: str, reason: str,
                           unconfirmed: bool = False, baseline: Optional[int] = None) -> None:
        if n <= 0:
            return
        self.pending[it.name] += n
        async with get_async_session() as session:
            session.add(PendingExchangeDelivery(
                session_id=self.id,
                twitch_user_id=self.user_id,
                user_login=self.user_login,
                channel_id=str(self.channel.channel_id),
                item_id=it.id,
                item_name=it.name,
                count=n,
                kind=kind,
                reason=reason,
                unconfirmed=unconfirmed,
                baseline=baseline,
                created_at=datetime.now(),
            ))
        self.log.info(f"Saved pending {kind}: {n}x {it.name} ({reason}{', unconfirmed' if unconfirmed else ''})")

    async def _return_received(self, items: Dict[Item, int]) -> Dict[Item, GiveResult]:
        results = {}
        for it, n in items.items():
            if n <= 0:
                continue
            res = await self.give_items(it, n, "refund")
            results[it] = res
            if res.failed:
                # Kept so the delivery routine retries it, and someone looks at it
                await self._add_pending(it, res.failed, "refund", "return failed")
                await self.say(
                    f"❌ Could not return {res.failed:,}× {it.name} to @{self.user_login}. "
                    f"{owner_ping()} (Exchange {self.id})"
                )
        return results

    # ---- endings

    async def _finish(self) -> None:
        await self.set_status("sending")
        results = {it: await self.give_items(it, n, "output") for it, n in self.get.items()}
        failed = {it: r.failed for it, r in results.items() if r.failed}
        excess = {it: self.received[it] - n for it, n in self.give.items() if self.received[it] > n}

        if not failed:
            async with get_async_session() as session:
                balance = await get_user_credits(session, self.user_id)
                if balance < FEE:
                    self.log.warning(f"Balance {balance} is below the fee now; taking it anyway")
                self.fee_trans_id = await add_credits(
                    session, self.user_id, -FEE,
                    f"Item exchange: {self.direction} {self.item.name} x{self.count} ({self.id})",
                )
            self.log.info(f"Fee taken: {FEE} credits, transaction {self.fee_trans_id}")
            self.outcome = "completed"
            await self.set_status("completed")
            pending = {it: r.pending for it, r in results.items() if r.pending}
            unconfirmed = {it: r.unconfirmed for it, r in results.items() if r.unconfirmed}
            delivered = {it: r.delivered for it, r in results.items() if r.delivered}
            if pending:
                await self.say(
                    f"Exchange done. {fmt_items(pending)} will be gifted when you rejoin"
                    + (f" (you already received {fmt_items(delivered)})" if delivered else "")
                    + f". (ID: {self.fee_trans_id}) (Exchange {self.id})",
                    reply=False,
                )
            elif not unconfirmed:
                await self.say(f"You have been given {fmt_items(self.get)}. (ID: {self.fee_trans_id}) (Exchange {self.id})")
            else:
                await self.say(
                    f"Exchange done. You received {fmt_items(delivered)}; the rest is being checked. "
                    f"(ID: {self.fee_trans_id}) (Exchange {self.id})"
                )
        else:
            # Pay back what wasn't covered
            if self.direction == "craft":
                missing_units = failed.get(self.item, 0)
            else:
                recipe = recipe_of(self.item)
                covered = min(
                    (results[ing].delivered + results[ing].pending + results[ing].unconfirmed) // per
                    for ing, per in recipe.items()
                )
                missing_units = self.count - covered
            refund = {
                it: n * missing_units // self.count for it, n in self.give.items()
            }
            self.log.warning(
                f"Partial delivery: failed {by_name(failed)}; {missing_units}/{self.count} not delivered; "
                f"returning {by_name(refund)}; no fee"
            )
            self.outcome = "partial"
            await self.set_status("partial")
            delivered = {it: r.delivered for it, r in results.items() if r.delivered}
            await self.say(
                f"There was not enough stock to finish your exchange. "
                + (f"You received {fmt_items(delivered)}. " if delivered else "")
                + f"Returning {fmt_items(refund)}. No fee was taken. (Exchange {self.id})"
            )
            await self._return_received(refund)

        if excess:
            self.log.info(f"Returning extra: {by_name(excess)}")
            results = await self._return_received(excess)
            if all(not r.failed and not r.unconfirmed and not r.pending for r in results.values()):
                await self.say(f"Returned the extra {fmt_items(excess)} you sent. (Exchange {self.id})")
            elif any(r.pending for r in results.values()):
                await self.say(f"The extra {fmt_items(excess)} you sent will be returned when you rejoin. (Exchange {self.id})", reply=False)
        await self.save()

    async def _check_missed_gifts(self) -> None:
        """Count gifts whose reply never arrived, from the user's and the stock's counts."""
        if not self.user_present or not any(self.received[it] < n for it, n in self.give.items()):
            return
        try:
            stock_now = await get_sendable_stock(self.channel, self.user_login)
        except Exception:
            self.log.warning("Could not read stock for the final check", exc_info=True)
            return
        for it, n in self.give.items():
            if self.received[it] >= n:
                continue
            try:
                now = await query_player_item_count(self.channel, self.user_login, it, log=self.log)
            except RecipientNotFoundError:
                self.log.info("User left before the final inventory check")
                return
            if now is None:
                self.log.info(f"Final check of {it.name}: no answer")
                continue
            user_drop = self.baseline[it] - now
            stock_gain = stock_now.get(it.id, 0) - self.stock_before.get(it.id, 0)
            # Both sides have to agree, so selling or using items can't be passed off as a gift
            missed = max(0, min(user_drop - self.received[it], stock_gain - self.received[it], n - self.received[it]))
            self.log.info(
                f"Final check of {it.name}: user {self.baseline[it]} -> {now} (dropped {user_drop}), "
                f"stock gained {stock_gain}, gifts counted {self.received[it]}: {missed} missed"
            )
            if missed:
                self.received[it] += missed
        await self.save()

    async def _abort(self) -> None:
        await self._check_missed_gifts()
        self.status = {"cancelled": "cancelled", "expired": "expired"}.get(self.outcome, "timed_out")
        await self.set_status(self.status)
        received = {it: n for it, n in self.received.items() if n}
        what = {
            "cancelled": "Exchange cancelled.",
            "expired": "Your exchange ran out of time.",
        }.get(self.outcome, "The exchange timed out.")
        if not received:
            await self.say(f"{what} (Exchange {self.id})")
            return
        results = await self._return_received(received)
        if any(r.pending for r in results.values()):
            await self.say(
                f"{what} Your {fmt_items(received)} will be returned when you rejoin. (Exchange {self.id})", reply=False
            )
        elif all(not r.failed and not r.unconfirmed for r in results.values()):
            await self.say(f"{what} Your {fmt_items(received)} were returned. (Exchange {self.id})")
        else:
            # The failures have already pinged the owner
            returned = {it: r.delivered for it, r in results.items() if r.delivered}
            await self.say(
                f"{what} " + (f"Returned {fmt_items(returned)}. " if returned else "")
                + f"The rest will be sorted out. (Exchange {self.id})"
            )


# ---------------------------------------------------------------- the cog

class ExchangeCog(Cog):
    """Trade ingredients for crafted items from stock and back."""

    def __init__(self, event_manager):
        super().__init__(event_manager)
        self.active: Dict[str, ExchangeRun] = {}
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._delivery_locks: Dict[str, asyncio.Lock] = {}
        self._pinged_rows: set[int] = set()
        self._rng = random.Random()
        install_db_log_handler()

    async def setup(self):
        self.main_loop = asyncio.get_running_loop()
        try:
            await self.recover_interrupted()
        except Exception:
            exchange_logger.error("Could not recover exchanges interrupted by a restart", exc_info=True)
        self.flush_logs.start()
        self.deliver_pending.start()
        self.purge.start()

    async def stop(self):
        # Open sessions are left as they are: recover_interrupted() settles them on the next start
        for r in (self.flush_logs, self.deliver_pending, self.purge):
            r.cancel()
        for run in list(self.active.values()):
            run.log.warning(f"Bot stopping during the exchange (status {run.status})")
        await flush_exchange_logs()

    async def recover_interrupted(self) -> None:
        """Settle sessions that were open when the bot stopped.

        Items received while waiting are saved to be returned. A session that was
        sending its side is left for the owner, because what the last gift did is unknown.
        """
        open_sessions = await load_rows(select(ExchangeSession).where(ExchangeSession.status.in_(OPEN_STATUSES)))
        for ex in open_sessions:
            log = SessionLogAdapter(ex.id, ex.user_login, ex.channel_name or "")
            if ex.status == "sending":
                log.error(
                    f"The bot stopped while this exchange was sending. received={ex.received} sent={ex.sent} "
                    f"returned={ex.returned}. Needs checking by hand."
                )
                status = "interrupted_sending"
            else:
                owed = {name: n for name, n in (ex.received or {}).items() if n > 0}
                async with get_async_session() as session:
                    for name, n in owed.items():
                        item = ravenpy._items_name_data.get(name)
                        if item is None:
                            log.error(f"Can't return {n}x {name}: unknown item")
                            continue
                        session.add(PendingExchangeDelivery(
                            session_id=ex.id, twitch_user_id=ex.twitch_user_id, user_login=ex.user_login,
                            channel_id=ex.channel_id, item_id=item.id, item_name=item.name, count=n,
                            kind="refund", reason="bot restarted", unconfirmed=False, created_at=datetime.now(),
                        ))
                log.warning(f"The bot stopped during this exchange ({ex.status}); returning {owed or 'nothing'}")
                status = "interrupted"
            async with get_async_session() as session:
                db_ex = await session.get(ExchangeSession, ex.id)
                db_ex.status = status
                db_ex.ended_at = datetime.now()
                db_ex.updated_at = datetime.now()
            await self._set_session_pending(ex.id)
        await flush_exchange_logs()

    def pick_account(self, accounts: List[str]) -> Optional[str]:
        return self._rng.choice(accounts) if accounts else None

    # ---- routines

    @routine(delta=timedelta(seconds=LOG_FLUSH_INTERVAL), wait_remainder=True, max_attempts=None)
    async def flush_logs(self):
        await flush_exchange_logs()

    @routine(delta=timedelta(hours=24), wait_remainder=True, max_attempts=None)
    async def purge(self):
        await flush_exchange_logs()
        await purge_old_exchanges()

    @routine(delta=timedelta(seconds=DELIVERY_INTERVAL), wait_remainder=True, max_attempts=None)
    async def deliver_pending(self):
        manager = self.global_context.ravenfall_manager
        if not manager:
            return
        rows = await load_rows(select(PendingExchangeDelivery))
        if not rows:
            return
        by_channel: Dict[str, List[PendingExchangeDelivery]] = {}
        for row in rows:
            by_channel.setdefault(row.channel_id, []).append(row)
        for channel_id, channel_rows in by_channel.items():
            channel = manager.get_channel(channel_id=channel_id)
            if channel is None or channel.sub_event == RFChannelSubEvent.DUNGEON_PREPARE:
                continue
            names = await player_names(channel)
            if not names:
                continue
            by_user: Dict[str, List[PendingExchangeDelivery]] = {}
            for row in channel_rows:
                by_user.setdefault(row.user_login.lower(), []).append(row)
            for login, user_rows in by_user.items():
                if login not in names:
                    continue
                # A user with an open exchange gets everything at the end of it
                if any(r.user_login == login for r in self.active.values()):
                    continue
                lock = self._delivery_locks.setdefault(login, asyncio.Lock())
                async with lock:
                    await self._deliver_rows(channel, login, user_rows)

    async def _deliver_rows(self, channel, login: str, rows: List[PendingExchangeDelivery]) -> None:
        delivered: Dict[str, Counter] = {"output": Counter(), "refund": Counter()}
        session_ids = set()
        for row in rows:
            if channel.sub_event == RFChannelSubEvent.DUNGEON_PREPARE:
                break
            log = SessionLogAdapter(row.session_id or "EX-?", login, channel.channel_name)
            item = ravenpy._items_id_data.get(row.item_id)
            if item is None:
                log.error(f"Pending delivery {row.id}: unknown item {row.item_id}")
                continue
            try:
                got, gone = await self._deliver_row(channel, login, row, item, log)
            except Exception:
                log.error(f"Pending delivery {row.id} failed", exc_info=True)
                continue
            if got:
                delivered[row.kind][item.name] += got
                session_ids.add(row.session_id)
                await self._bump_session(row.session_id, "sent" if row.kind == "output" else "returned", item.name, got)
            if gone:
                break  # the user left again
        ids = ", ".join(sorted(s for s in session_ids if s))
        if delivered["output"]:
            await self._say(channel, f"@{login} Delivered your {fmt_items(delivered['output'])} from your exchange. ({ids})")
        if delivered["refund"]:
            await self._say(channel, f"@{login} Returned your {fmt_items(delivered['refund'])} from your exchange. ({ids})")

    async def _deliver_row(self, channel, login: str, row: PendingExchangeDelivery, item: Item, log) -> tuple[int, bool]:
        """Try to settle one row. Returns (items now with the user, whether the user is gone)."""
        got = 0
        if row.unconfirmed:
            try:
                now = await query_player_item_count(channel, login, item, log=log)
            except RecipientNotFoundError:
                return 0, True
            if now is None:
                log.info(f"Pending row {row.id}: no answer to !count, will retry")
                return 0, False
            arrived = max(0, min(row.count, now - (row.baseline or 0)))
            log.info(
                f"Pending row {row.id}: unconfirmed {row.count}x {item.name}; {row.baseline} before, {now} now: "
                f"{arrived} arrived"
            )
            got += arrived
            await self._update_row(row, count=row.count - arrived, unconfirmed=False, baseline=None)
            if row.count <= 0:
                return got, False

        log.info(f"Pending row {row.id}: gifting {row.count}x {item.name} ({row.kind})")
        n = row.count
        try:
            await redeem_rf.send_items(login, channel, item.name, n, verify_recipient=True, log=log)
            await self._update_row(row, count=0)
            return got + n, False
        except PartialSendError as e:
            await self._update_row(row, count=n - e.items_sent)
            got += e.items_sent
            await self._out_of_stock(channel, login, row, item, log)
            return got, False
        except OutOfItemsError:
            await self._out_of_stock(channel, login, row, item, log)
            return got, False
        except RecipientNotFoundError as e:
            await self._update_row(row, count=n - e.items_sent)
            return got + e.items_sent, True
        except UnconfirmedSendError as e:
            rest = n - e.items_sent - e.unconfirmed
            got += e.items_sent
            if e.recipient_absent:
                await self._update_row(row, count=e.unconfirmed, unconfirmed=True, baseline=e.baseline)
                if rest > 0:
                    await self._copy_row(row, rest)
                return got, True
            await self._update_row(row, count=rest)
            log.error(f"Pending row {row.id}: could not confirm {e.unconfirmed}x {item.name}")
            await self._say(
                channel,
                f"❌ Could not confirm {e.unconfirmed:,}× {item.name} sent to @{login}. {owner_ping()} ({row.session_id})",
            )
            return got, False
        except Exception as e:
            sent = getattr(e, "items_sent", 0)
            if sent:
                await self._update_row(row, count=n - sent)
            log.warning(f"Pending row {row.id}: {e!r}", exc_info=not isinstance(e, (CouldNotSendMessageError, CouldNotSendItemsError, redeem_rf.TimeoutError)))
            await self._ping_once(channel, row, f"❌ Could not deliver {row.count:,}× {item.name} to @{login}.")
            return got + sent, False

    async def _out_of_stock(self, channel, login: str, row: PendingExchangeDelivery, item: Item, log) -> None:
        if row.count <= 0:
            return
        ex = None
        if row.session_id:
            found = await load_rows(select(ExchangeSession).where(ExchangeSession.id == row.session_id))
            ex = found[0] if found else None
        crafted = ravenpy._items_id_data.get(ex.item_id) if ex else None
        if row.kind == "output" and ex is not None and ex.direction == "craft" and crafted is not None and crafted.id == item.id:
            # The crafted item ran out while they were away: give the ingredients back instead
            log.warning(f"Pending row {row.id}: out of {item.name}; returning the ingredients and the fee")
            for ing, per in recipe_of(crafted).items():
                await self._copy_row(row, per * row.count, item=ing, kind="refund", reason="output out of stock")
            if ex.fee_trans_id and not ex.refund_trans_id:
                async with get_async_session() as session:
                    trans_id = await add_credits(session, row.twitch_user_id, FEE, f"Item exchange fee refund ({row.session_id})")
                    db_ex = await session.get(ExchangeSession, row.session_id)
                    db_ex.refund_trans_id = trans_id
                log.info(f"Fee refunded, transaction {trans_id}")
                await self._say(
                    channel,
                    f"@{login} {row.count:,}× {item.name} ran out while you were away. You'll get the ingredients "
                    f"back and your {FEE} credits were refunded. (ID: {trans_id}) ({row.session_id})",
                )
            await self._update_row(row, count=0)
            return
        log.warning(f"Pending row {row.id}: out of stock of {item.name}")
        await self._ping_once(channel, row, f"❌ Out of stock: {row.count:,}× {item.name} owed to @{login}.")

    async def _ping_once(self, channel, row: PendingExchangeDelivery, text: str) -> None:
        if row.id in self._pinged_rows:
            return
        self._pinged_rows.add(row.id)
        await self._say(channel, f"{text} {owner_ping()} ({row.session_id})")

    async def _say(self, channel, text: str) -> None:
        try:
            await channel.send_chat_message(text)
        except Exception:
            exchange_logger.warning(f"Could not send chat message: {text!r}", exc_info=True)

    async def _update_row(self, row: PendingExchangeDelivery, **values) -> None:
        for k, v in values.items():
            setattr(row, k, v)
        async with get_async_session() as session:
            db_row = await session.get(PendingExchangeDelivery, row.id)
            if db_row is None:
                return
            if row.count <= 0:
                await session.delete(db_row)
            else:
                for k, v in values.items():
                    setattr(db_row, k, v)
        if row.count <= 0 or "count" in values:
            await self._set_session_pending(row.session_id)

    async def _copy_row(self, row: PendingExchangeDelivery, count: int, item: Item | None = None,
                        kind: str | None = None, reason: str | None = None) -> None:
        async with get_async_session() as session:
            session.add(PendingExchangeDelivery(
                session_id=row.session_id,
                twitch_user_id=row.twitch_user_id,
                user_login=row.user_login,
                channel_id=row.channel_id,
                item_id=item.id if item else row.item_id,
                item_name=item.name if item else row.item_name,
                count=count,
                kind=kind or row.kind,
                reason=reason or row.reason,
                unconfirmed=False,
                created_at=datetime.now(),
            ))
        await self._set_session_pending(row.session_id)

    async def _set_session_pending(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        async with get_async_session() as session:
            ex = await session.get(ExchangeSession, session_id)
            if ex is None:
                return
            rows = (await session.execute(
                select(PendingExchangeDelivery).where(PendingExchangeDelivery.session_id == session_id)
            )).scalars()
            pending: Counter = Counter()
            for r in rows:
                if r.count > 0:
                    pending[r.item_name] += r.count
            ex.pending = dict(pending)
            ex.updated_at = datetime.now()

    async def _bump_session(self, session_id: Optional[str], field: str, item_name: str, n: int) -> None:
        if not session_id or n <= 0:
            return
        async with get_async_session() as session:
            ex = await session.get(ExchangeSession, session_id)
            if ex is None:
                return
            values = dict(getattr(ex, field) or {})
            values[item_name] = values.get(item_name, 0) + n
            setattr(ex, field, values)
            ex.updated_at = datetime.now()

    # ---- starting

    def _start(self, run: ExchangeRun) -> None:
        loop = self.main_loop
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if loop is None or loop is current:
            asyncio.create_task(run.run())
        else:
            asyncio.run_coroutine_threadsafe(run.run(), loop)

    async def _begin(self, ctx: CommandEvent, item: Item, count: int, channel, direction: str):
        user_id = str(ctx.message.author_id)
        existing = self.active.get(user_id)
        if existing is not None:
            await ctx.message.reply(f"You already have an exchange in progress. ({existing.id})")
            return
        problem = exchange_problem(item)
        if problem:
            await ctx.message.reply(problem)
            return
        run = ExchangeRun(self, channel, ctx.message, item, count, direction)
        self.active[user_id] = run
        run.log.info(f"Command: {ctx.prefix}{ctx.invoked_with} {ctx.parameters_text}")
        self._start(run)

    # ---- commands

    @command(name="exchange craft", aliases=["ex craft", "trade craft", "excraft", "exchangecraft", "tradecraft"])
    @parameter("item", regex=ITEM_REGEX, converter=RFItemConverter)
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("count", converter=RangeInt(1, None))
    @checks(TwitchOnly)
    async def exchange_craft(self, ctx: CommandEvent, item: Item, count: int = 1, *, channel: RFChannel = 'this'):
        """Gift the ingredients for an item and get the crafted item from stock.

        Args:
            item: The crafted item you want.
            count: How many.
            channel: Target channel.
        """
        await self._begin(ctx, item, count, channel, "craft")

    @command(name="exchange uncraft", aliases=["ex uncraft", "trade uncraft", "exuncraft", "exchangeuncraft", "tradeuncraft"])
    @parameter("item", regex=ITEM_REGEX, converter=RFItemConverter)
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("count", converter=RangeInt(1, None))
    @checks(TwitchOnly)
    async def exchange_uncraft(self, ctx: CommandEvent, item: Item, count: int = 1, *, channel: RFChannel = 'this'):
        """Gift a crafted item and get its ingredients from stock.

        Args:
            item: The crafted item you are giving.
            count: How many.
            channel: Target channel.
        """
        await self._begin(ctx, item, count, channel, "uncraft")

    @command(name="exchange cancel", aliases=["ex cancel", "trade cancel", "excancel", "exchangecancel"])
    @checks(TwitchOnly)
    async def exchange_cancel(self, ctx: CommandEvent):
        """Stop your exchange and get back what you've gifted so far."""
        run = self.active.get(str(ctx.message.author_id))
        if run is None:
            await ctx.message.reply("You don't have an exchange in progress.")
            return
        if run.status not in ("waiting", "paused", "checking"):
            await ctx.message.reply("Your exchange is already being sent and can't be cancelled.")
            return
        run.log.info("Cancel requested")
        run.request_cancel()

    @command(name="exchange pending", aliases=["exchange refunds", "exchange owed", "ex pending", "ex refunds", "ex owed"])
    @checks(TwitchOnly)
    async def exchange_pending(self, ctx: CommandEvent, user: TwitchUsername = None):
        """List items your exchanges still owe you.

        Args:
            user: (Admins) another user.
        """
        login = ctx.message.author_login.lower()
        if user is not None and user.lower() != login:
            if not any(r.level() >= UserRole.ADMINISTRATOR.level() for r in ctx.message.author_roles):
                await ctx.message.reply("Only admins can look at other users' exchanges.")
                return
            login = user.lower()
        rows = await load_rows(select(PendingExchangeDelivery).where(PendingExchangeDelivery.user_login == login))
        if not rows:
            await ctx.message.reply(f"{'You have' if login == ctx.message.author_login.lower() else login + ' has'} nothing owed from exchanges.")
            return
        owed: Dict[str, Counter] = {"output": Counter(), "refund": Counter()}
        for r in rows:
            owed.setdefault(r.kind, Counter())[r.item_name] += r.count
        parts = []
        if owed["output"]:
            parts.append(f"to deliver: {fmt_items(dict(owed['output']))}")
        if owed["refund"]:
            parts.append(f"to return: {fmt_items(dict(owed['refund']))}")
        sessions = ", ".join(sorted({r.session_id for r in rows if r.session_id}))
        await ctx.message.reply(f"Owed from exchanges ({sessions}): {'; '.join(parts)}. They are gifted when you're in the game.")

    @command(name="exchange log", aliases=["ex log"])
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def exchange_log(self, ctx: CommandEvent, session_id: str):
        """Upload everything logged for an exchange.

        Args:
            session_id: The exchange ID, e.g. EX-7F3A9C.
        """
        session_id = session_id.upper()
        if not session_id.startswith("EX-"):
            session_id = "EX-" + session_id
        await flush_exchange_logs()
        text = await self._session_log_text([session_id])
        if text is None:
            await ctx.message.reply("No exchange found with that ID (sessions older than 2 months are deleted).")
            return
        url = await upload_to_bin(text)
        await ctx.message.reply(f"Exchange {session_id}: {url}")

    @command(name="exchange log user", aliases=["ex log user"])
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def exchange_log_user(self, ctx: CommandEvent, user: TwitchUsername):
        """Upload the logs of a user's exchanges from the last 2 months.

        Args:
            user: Twitch username.
        """
        await flush_exchange_logs()
        since = datetime.now() - LOG_LOOKBACK
        async with get_async_session() as session:
            ids = [r[0] for r in (await session.execute(
                select(ExchangeSession.id)
                .where(ExchangeSession.user_login == user.lower(), ExchangeSession.created_at >= since)
                .order_by(ExchangeSession.created_at.desc())
            )).all()]
        if not ids:
            await ctx.message.reply(f"No exchanges by {user} in the last 2 months.")
            return
        text = await self._session_log_text(ids)
        url = await upload_to_bin(text)
        await ctx.message.reply(f"{len(ids)} {pl(len(ids), 'exchange', 'exchanges')} by {user}: {url}")

    async def _session_log_text(self, ids: List[str]) -> Optional[str]:
        out = []
        found = False
        async with get_async_session() as session:
            sessions = {s.id: s for s in (await session.execute(
                select(ExchangeSession).where(ExchangeSession.id.in_(ids))
            )).scalars()}
            if len(ids) > 1:
                out.append("Summary")
                for sid in ids:
                    s = sessions.get(sid)
                    if s:
                        out.append(f"  {s.id}  {s.created_at:%Y-%m-%d %H:%M}  {s.direction} {s.item_name} x{s.count}  {s.status}")
                out.append("")
            for sid in ids:
                s = sessions.get(sid)
                if s is None:
                    continue
                found = True
                out.append("=" * 80)
                out.append(f"{s.id}  {s.direction} {s.item_name} x{s.count}  user={s.user_login} ({s.twitch_user_id})  channel={s.channel_name}")
                out.append(f"status={s.status}  created={s.created_at}  ended={s.ended_at}  paused={s.paused_seconds}s")
                out.append(f"required={s.required}  received={s.received}")
                out.append(f"sent={s.sent}  returned={s.returned}  pending={s.pending}")
                out.append(f"baseline={s.baseline}  suggested={s.suggested_account}  fee_trans={s.fee_trans_id}  refund_trans={s.refund_trans_id}")
                out.append("")
                entries = (await session.execute(
                    select(ExchangeLogEntry)
                    .where(ExchangeLogEntry.session_id == sid)
                    .order_by(ExchangeLogEntry.timestamp, ExchangeLogEntry.id)
                )).scalars()
                for e in entries:
                    out.append(f"[{e.timestamp:%Y-%m-%d %H:%M:%S.%f}] [{e.level:<8}] {e.message}")
                    if e.exc_text:
                        out.append(e.exc_text)
                out.append("")
        return "\n".join(out) if found else None

    @command(name="exchange", aliases=["ex", "trade"])
    @parameter("item", regex=ITEM_REGEX, converter=RFItemConverter)
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def exchange_info(self, ctx: CommandEvent, item: Item, *, channel: RFChannel = 'this'):
        """Show what an item exchanges for and what's in stock.

        Args:
            item: A crafted item.
            channel: Target channel.
        """
        problem = exchange_problem(item)
        if problem:
            await ctx.message.reply(problem)
            return
        stock = await get_sendable_stock(channel, ctx.message.author_login)
        recipe = recipe_of(item)
        await ctx.message.reply(
            f"1× {item.name} = {fmt_items(recipe)}. In stock: {stock.get(item.id, 0):,}× {item.name}; "
            + ", ".join(f"{stock.get(ing.id, 0):,}× {ing.name}" for ing in recipe)
            + f". Fee: {FEE} credits. Use !exchange craft or !exchange uncraft."
        )
