"""Run with: python -m unittest tests.test_item_exchange

!exchange: users gift ingredients to a stock account for a crafted item from
stock, or the reverse. These run sessions against a fake town and a temporary
database, with the game and multichat mocked out.
"""

import asyncio
import logging
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from bot import exchange_log
from bot.cogs import exchange, redeem_rf
from bot.cogs.redeem_rf import (
    OutOfItemsError, PartialSendError, RavenfallResponse, RecipientNotFoundError, UnconfirmedSendError,
)
from bot.models import RFChannelSubEvent
from database.models import (
    Base, ExchangeLogEntry, ExchangeSession, PendingExchangeDelivery, UserCreditTransaction,
)
from database.utils import add_credits, get_user_credits
from ravenpy import ravenpy
from shared_loc import shared_localization

GIFT = "You gifted {giftCount}x {itemName} to {player}!"
LEAVE = "You have left the game."


def pick_craftable(min_ingredients=2):
    for item in ravenpy.get_all_items():
        if (len(item.craft_ingredients) >= min_ingredients and not item.soulbound
                and all(not i.item.soulbound and i.amount > 0 for i in item.craft_ingredients)):
            return item
    raise unittest.SkipTest("No craftable item with enough ingredients")


ITEM = pick_craftable()
RECIPE = {ing.item: ing.amount for ing in ITEM.craft_ingredients}


class FakeTown:
    def __init__(self):
        self.channel_id = "ex-test"
        self.channel_name = "town"
        self.sub_event = RFChannelSubEvent.NONE
        self.ravenfall_message_listeners = []
        self.rfloc = shared_localization()
        self.players = {"viewer", "stockbot", "stockalt"}
        self.said = []
        self.inventory = {}  # item name -> the user's count
        self.stock = {}  # item id -> sendable stock

    async def send_chat_message(self, text, ignore_error=False, reply_id=None):
        self.said.append(text)

    async def get_players(self):
        return [{"name": n} for n in self.players]

    def emit(self, fmt, args, sender="viewer"):
        message = {"Format": fmt, "Args": args, "Recipent": {"PlatformUserName": sender}}
        for listener in list(self.ravenfall_message_listeners):
            listener(message)

    def gift(self, item, n, to="StockBot", sender="viewer"):
        self.emit(GIFT, [n, item.name, to], sender)

    def text(self):
        return "\n".join(self.said)


class FakeCog:
    def __init__(self):
        self.active = {}

    def pick_account(self, accounts):
        return accounts[0] if accounts else None


def make_message(login="viewer"):
    return SimpleNamespace(author_login=login, author_name=login.capitalize(), author_id="123", id="msg-1")


class DatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    """A temporary database behind get_async_session in the exchange modules."""

    async def asyncSetUp(self):
        # In memory: a file database costs about a second per test on Windows
        self.engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        @asynccontextmanager
        async def session_factory():
            async with AsyncSession(self.engine) as session:
                try:
                    yield session
                    await session.commit()
                except BaseException:
                    await session.rollback()
                    raise

        self.session = session_factory
        self.patches = [
            mock.patch.object(exchange, "get_async_session", session_factory),
            mock.patch.object(exchange_log, "get_async_session", session_factory),
        ]
        for p in self.patches:
            p.start()
        # As main.py's setup_logging and the cog do
        self.logger_level = exchange_log.exchange_logger.level
        exchange_log.exchange_logger.setLevel(logging.DEBUG)
        exchange_log.install_db_log_handler()
        # Lines from other tests are not wanted in this database
        exchange_log.db_log_handler.take(10**6)

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        exchange_log.exchange_logger.setLevel(self.logger_level)
        exchange_log.db_log_handler.take(10**6)
        await self.engine.dispose()

    async def rows(self, model):
        return await exchange.load_rows(select(model))


class SessionTestCase(DatabaseTestCase):
    """Runs ExchangeRun against FakeTown with short timers."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.town = FakeTown()
        self.cog = FakeCog()
        self.send_items = mock.AsyncMock()

        async def count(channel, user, item, timeout=10, log=None):
            if user not in channel.players:
                raise RecipientNotFoundError("not in the game")
            return channel.inventory.get(item.name, 0)

        async def stock(channel, exclude_login=""):
            return dict(channel.stock)

        async def accounts(channel, exclude_login=""):
            return {"stockbot": {"stockbot"}, "stockalt": {"stockalt", "stock_alt"}}

        self.session_patches = [
            mock.patch.object(exchange, "GIFT_WINDOW", 0.6),
            mock.patch.object(exchange, "POLL_INTERVAL", 0.05),
            mock.patch.object(exchange, "MAX_SESSION", 5.0),
            mock.patch.object(exchange, "query_player_item_count", count),
            mock.patch.object(exchange, "get_sendable_stock", stock),
            mock.patch.object(exchange, "get_stock_account_names", accounts),
            mock.patch.object(redeem_rf, "send_items", self.send_items),
        ]
        for p in self.session_patches:
            p.start()
        async with self.session() as session:
            await add_credits(session, "123", 100, "test")

    async def asyncTearDown(self):
        for p in self.session_patches:
            p.stop()
        await super().asyncTearDown()

    def stock_everything(self, n=1000):
        self.town.stock = {ITEM.id: n, **{ing.id: n for ing in RECIPE}}

    def start(self, direction="craft", count=1):
        run = exchange.ExchangeRun(self.cog, self.town, make_message(), ITEM, count, direction)
        self.cog.active[run.user_id] = run
        task = asyncio.create_task(run.run())
        return run, task

    async def wait_status(self, run, *statuses, timeout=3.0):
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while run.status not in statuses:
            if loop.time() > end:
                self.fail(f"status stayed {run.status}, wanted {statuses}")
            await asyncio.sleep(0.01)

    async def wait_said(self, text, timeout=3.0):
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while text not in self.town.text():
            if loop.time() > end:
                self.fail(f"never said {text!r}; said {self.town.said!r}")
            await asyncio.sleep(0.01)

    async def started(self, direction="craft", count=1):
        """Start a session and wait for the instructions."""
        run, task = self.start(direction, count)
        await self.wait_said("Type !exchange cancel")
        return run, task

    def sent_items(self):
        """(item name, count) for every send_items call."""
        return [(c.args[2], c.args[3]) for c in self.send_items.await_args_list]

    async def credits(self):
        async with self.session() as session:
            return await get_user_credits(session, "123")


class RuleTests(unittest.TestCase):
    def test_sides_scale_with_count(self):
        give, get = exchange.sides(ITEM, 3, "craft")
        self.assertEqual(give, {ing: n * 3 for ing, n in RECIPE.items()})
        self.assertEqual(get, {ITEM: 3})
        give, get = exchange.sides(ITEM, 2, "uncraft")
        self.assertEqual(give, {ITEM: 2})
        self.assertEqual(get, {ing: n * 2 for ing, n in RECIPE.items()})

    def test_uncraftable_and_soulbound_items_are_refused(self):
        raw = next(i for i in ravenpy.get_all_items() if not i.craft_ingredients)
        self.assertIn("not crafted", exchange.exchange_problem(raw))
        soulbound = SimpleNamespace(name="Bound", soulbound=True, craft_ingredients=ITEM.craft_ingredients)
        self.assertIn("soulbound", exchange.exchange_problem(soulbound))
        self.assertIsNone(exchange.exchange_problem(ITEM))


class GiftMatchingTests(SessionTestCase):
    def make_run(self):
        run = exchange.ExchangeRun(self.cog, self.town, make_message(), ITEM, 1, "craft")
        run.account_names_seen = {"stockbot", "stockalt", "stock_alt"}
        return run

    def message(self, n, item_name, to, sender="viewer"):
        return {"Format": GIFT, "Args": [n, item_name, to], "Recipent": {"PlatformUserName": sender}}

    async def test_gifts_to_any_stock_account_count_and_add_up(self):
        run = self.make_run()
        ing, need = next(iter(RECIPE.items()))
        self.assertTrue(run._handle_message(self.message(1, ing.name, "StockBot")))
        self.assertTrue(run._handle_message(self.message(need, ing.name, "Stock_Alt")))  # display name
        self.assertEqual(run.received[ing], need + 1)

    async def test_gifts_that_do_not_count(self):
        run = self.make_run()
        ing = next(iter(RECIPE))
        with self.assertLogs("bot.exchange", logging.DEBUG) as logs:
            self.assertFalse(run._handle_message(self.message(5, ing.name, "someone_else")))
            self.assertFalse(run._handle_message(self.message(5, ing.name, "stockbot", sender="other")))
            self.assertFalse(run._handle_message(self.message(5, ITEM.name, "stockbot")))
        self.assertEqual(sum(run.received.values()), 0)
        self.assertTrue(any("not a stock account" in line for line in logs.output))
        self.assertTrue(any("not part of this exchange" in line for line in logs.output))
        self.assertTrue(all(run.id in line for line in logs.output))

    async def test_leave_and_kick_mark_the_user_away(self):
        run = self.make_run()
        run._handle_message({"Format": LEAVE, "Args": [], "Recipent": {"PlatformUserName": "viewer"}})
        self.assertFalse(run.user_present)
        run.user_present = True
        run._handle_message({"Format": "{player} was kicked from the game.", "Args": ["Viewer"], "Recipent": {}})
        self.assertFalse(run.user_present)


class ExchangeFlowTests(SessionTestCase):
    async def give_everything(self, run, extra=0):
        for ing, n in RECIPE.items():
            self.town.gift(ing, n + extra)

    async def test_craft_completes_takes_the_fee_once_and_returns_extra(self):
        self.stock_everything()
        self.town.inventory = {ing.name: 100 for ing in RECIPE}
        run, task = await self.started()
        self.assertIn(f"Exchange {run.id}", self.town.said[-1])
        await self.give_everything(run, extra=1)
        await asyncio.wait_for(task, 3)

        self.assertEqual(run.outcome, "completed")
        self.assertEqual(self.sent_items()[0], (ITEM.name, 1))
        self.assertEqual(sorted(self.sent_items()[1:]), sorted((ing.name, 1) for ing in RECIPE))
        for c in self.send_items.await_args_list:
            self.assertTrue(c.kwargs["verify_recipient"])
        self.assertEqual(await self.credits(), 100 - exchange.FEE)
        fees = [t for t in await self.rows(UserCreditTransaction) if t.credits == -exchange.FEE]
        self.assertEqual(len(fees), 1)
        (db,) = await self.rows(ExchangeSession)
        self.assertEqual(db.status, "completed")
        self.assertEqual(db.sent, {ITEM.name: 1})
        self.assertIn(f"(ID: {run.fee_trans_id})", self.town.text())

    async def test_uncraft_sends_the_ingredients(self):
        self.stock_everything()
        self.town.inventory = {ITEM.name: 5}
        run, task = await self.started("uncraft", 2)
        self.town.gift(ITEM, 2)
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "completed")
        self.assertEqual(sorted(self.sent_items()), sorted((ing.name, n * 2) for ing, n in RECIPE.items()))

    async def test_user_short_of_items_is_refused_without_a_fee(self):
        self.stock_everything()
        self.town.inventory = {}
        run, task = self.start()
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "rejected")
        self.assertIn("You don't have enough items", self.town.said[-1])
        self.send_items.assert_not_awaited()
        self.assertEqual(await self.credits(), 100)

    async def test_out_of_stock_is_refused(self):
        self.town.stock = {}
        self.town.inventory = {ing.name: 100 for ing in RECIPE}
        run, task = self.start()
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "rejected")
        self.assertIn("not enough items in stock", self.town.said[-1])

    async def test_too_few_credits_is_refused(self):
        self.stock_everything()
        self.town.inventory = {ing.name: 100 for ing in RECIPE}
        async with self.session() as session:
            await add_credits(session, "123", -95, "test")
        run, task = self.start()
        await asyncio.wait_for(task, 3)
        self.assertIn(f"You need {exchange.FEE} item credits", self.town.said[-1])

    async def test_timeout_returns_what_was_received_without_a_fee(self):
        self.stock_everything()
        ing, need = next(iter(RECIPE.items()))
        self.town.inventory = {i.name: 100 for i in RECIPE}
        run, task = await self.started()
        self.town.gift(ing, 1)
        self.town.inventory[ing.name] = 99
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "timed_out")
        self.assertEqual(self.sent_items(), [(ing.name, 1)])
        self.assertEqual(await self.credits(), 100)
        self.assertIn("timed out", self.town.said[-1])

    async def test_cancel_returns_what_was_received(self):
        self.stock_everything()
        ing = next(iter(RECIPE))
        self.town.inventory = {i.name: 100 for i in RECIPE}
        run, task = await self.started()
        self.town.gift(ing, 1)
        await asyncio.sleep(0.05)
        run.request_cancel()
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "cancelled")
        self.assertEqual(self.sent_items(), [(ing.name, 1)])

    async def test_missed_gift_reply_is_counted_when_user_and_stock_agree(self):
        self.stock_everything()
        ing, need = next(iter(RECIPE.items()))
        self.town.inventory = {i.name: 100 for i in RECIPE}
        run, task = await self.started()
        # They left the user and reached the stock, but no reply came through.
        # 5 more left the user than it needed: only what the exchange asked for counts.
        self.town.inventory[ing.name] = 100 - need - 5
        self.town.stock[ing.id] += need + 5
        await asyncio.wait_for(task, 3)
        self.assertEqual(self.sent_items(), [(ing.name, need)])

    async def test_items_the_user_got_rid_of_are_not_counted_as_gifts(self):
        self.stock_everything()
        ing = next(iter(RECIPE))
        self.town.inventory = {i.name: 100 for i in RECIPE}
        run, task = await self.started()
        self.town.inventory[ing.name] = 50  # sold, not gifted: stock didn't change
        await asyncio.wait_for(task, 3)
        self.send_items.assert_not_awaited()

    async def test_leaving_pauses_the_timer_and_rejoining_resumes_it(self):
        self.stock_everything()
        self.town.inventory = {i.name: 100 for i in RECIPE}
        run, task = await self.started()
        self.town.emit(LEAVE, [])
        self.town.players.discard("viewer")
        await asyncio.sleep(1.0)  # longer than the window
        self.assertFalse(task.done())
        self.assertIn("Your exchange is paused until you rejoin", self.town.text())
        self.town.players.add("viewer")
        await self.wait_said("Welcome back")
        for ing, n in RECIPE.items():
            self.town.gift(ing, n)
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "completed")
        self.assertGreater(run.paused_seconds, 0.5)

    async def test_session_limit_ends_a_paused_exchange(self):
        self.stock_everything()
        self.town.inventory = {i.name: 100 for i in RECIPE}
        with mock.patch.object(exchange, "MAX_SESSION", 0.5):
            run, task = await self.started()
            self.town.players.discard("viewer")
            await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "expired")

    async def test_suggested_account_leaving_points_to_another(self):
        self.stock_everything()
        self.town.inventory = {i.name: 100 for i in RECIPE}
        run, task = await self.started()
        first = run.suggested
        self.town.players.discard(first)
        for _ in range(100):
            if run.suggested != first:
                break
            await asyncio.sleep(0.01)
        self.assertNotEqual(run.suggested, first)
        self.assertIn(f"{first} left the game. Send the rest", self.town.text())
        # A gift to the account that left still counts
        for ing, n in RECIPE.items():
            self.town.gift(ing, n, to=first)
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "completed")

    async def test_no_stock_account_pauses(self):
        self.stock_everything()
        self.town.inventory = {i.name: 100 for i in RECIPE}
        run, task = await self.started()
        self.town.players -= {"stockbot", "stockalt"}
        await asyncio.sleep(1.0)
        self.assertFalse(task.done())
        self.assertIn("No exchange account is in the game", self.town.text())
        run.request_cancel()
        await asyncio.wait_for(task, 3)

    async def test_dungeon_preparation_pauses_the_timer(self):
        self.stock_everything()
        self.town.inventory = {i.name: 100 for i in RECIPE}
        run, task = await self.started()
        self.town.sub_event = RFChannelSubEvent.DUNGEON_PREPARE
        self.town.players.discard("viewer")  # ignored while preparing
        await asyncio.sleep(1.0)
        self.assertFalse(task.done())
        self.assertTrue(run.user_present)
        self.town.players.add("viewer")
        self.town.sub_event = RFChannelSubEvent.NONE
        for ing, n in RECIPE.items():
            self.town.gift(ing, n)
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "completed")

    async def test_finishing_while_the_user_is_away_saves_the_delivery(self):
        self.stock_everything()
        self.town.inventory = {i.name: 100 for i in RECIPE}
        self.send_items.side_effect = RecipientNotFoundError("gone", 0)
        run, task = await self.started()
        for ing, n in RECIPE.items():
            self.town.gift(ing, n)
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "completed")
        self.assertEqual(await self.credits(), 100 - exchange.FEE)
        (row,) = await self.rows(PendingExchangeDelivery)
        self.assertEqual((row.item_id, row.count, row.kind), (ITEM.id, 1, "output"))
        self.assertIn("will be gifted when you rejoin", self.town.text())

    async def test_partial_delivery_returns_the_matching_share_without_a_fee(self):
        self.stock_everything()
        self.town.inventory = {i.name: 100 for i in RECIPE}

        async def send(user, channel, name, n, **kwargs):
            if name == ITEM.name:
                raise PartialSendError("ran out", 1)

        self.send_items.side_effect = send
        run, task = await self.started(count=3)
        for ing, n in RECIPE.items():
            self.town.gift(ing, n * 3)
        await asyncio.wait_for(task, 3)
        self.assertEqual(run.outcome, "partial")
        self.assertEqual(await self.credits(), 100)
        self.assertEqual(sorted(self.sent_items()[1:]), sorted((ing.name, n * 2) for ing, n in RECIPE.items()))

    async def test_unconfirmed_gift_pings_the_owner_and_is_not_refunded(self):
        self.stock_everything()
        self.town.inventory = {i.name: 100 for i in RECIPE}
        self.send_items.side_effect = UnconfirmedSendError("?", 0, unconfirmed=1, baseline=0)
        run, task = await self.started()
        for ing, n in RECIPE.items():
            self.town.gift(ing, n)
        await asyncio.wait_for(task, 3)
        self.assertIn("Could not confirm", self.town.text())
        self.assertIn("pinging @", self.town.text())
        self.assertEqual(len(self.sent_items()), 1)  # nothing returned
        self.assertEqual(await self.credits(), 100 - exchange.FEE)

    async def test_session_lines_are_saved_with_a_summary(self):
        self.stock_everything()
        self.town.inventory = {}
        run, task = self.start()
        await asyncio.wait_for(task, 3)
        await exchange_log.flush_exchange_logs()
        lines = [e.message for e in await self.rows(ExchangeLogEntry)]
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith(run.id) for line in lines))
        self.assertTrue(any("summary:" in line and "result=rejected" in line for line in lines))


class PendingDeliveryTests(SessionTestCase):
    def make_cog(self):
        manager = SimpleNamespace(get_channel=lambda channel_id=None, channel_name=None: self.town)
        event_manager = SimpleNamespace(dispatchers={}, global_context=SimpleNamespace(ravenfall_manager=manager))
        return exchange.ExchangeCog(event_manager)

    async def add_row(self, **values):
        row = dict(session_id="EX-TEST01", twitch_user_id="123", user_login="viewer", channel_id="ex-test",
                   item_id=ITEM.id, item_name=ITEM.name, count=2, kind="output", reason="test",
                   unconfirmed=False, created_at=datetime.now())
        row.update(values)
        async with self.session() as session:
            session.add(PendingExchangeDelivery(**row))

    async def test_rows_are_gifted_when_the_user_is_back(self):
        await self.add_row()
        cog = self.make_cog()
        self.town.players.discard("viewer")
        await cog.deliver_pending()
        self.send_items.assert_not_awaited()
        self.town.players.add("viewer")
        await cog.deliver_pending()
        self.assertEqual(self.sent_items(), [(ITEM.name, 2)])
        self.assertEqual(await self.rows(PendingExchangeDelivery), [])
        self.assertIn("Delivered your 2× ", self.town.text())

    async def test_partial_send_lowers_the_row(self):
        await self.add_row(kind="refund")
        self.send_items.side_effect = PartialSendError("ran out", 1)
        await self.make_cog().deliver_pending()
        (row,) = await self.rows(PendingExchangeDelivery)
        self.assertEqual(row.count, 1)

    async def test_skipped_during_dungeon_preparation(self):
        await self.add_row()
        self.town.sub_event = RFChannelSubEvent.DUNGEON_PREPARE
        await self.make_cog().deliver_pending()
        self.send_items.assert_not_awaited()

    async def test_unconfirmed_row_is_checked_against_its_baseline(self):
        await self.add_row(count=3, unconfirmed=True, baseline=10)
        self.town.inventory = {ITEM.name: 12}  # 2 of the 3 arrived
        await self.make_cog().deliver_pending()
        self.assertEqual(self.sent_items(), [(ITEM.name, 1)])
        self.assertEqual(await self.rows(PendingExchangeDelivery), [])

    async def test_crafted_item_out_of_stock_becomes_ingredients_and_a_fee_refund(self):
        async with self.session() as session:
            session.add(ExchangeSession(
                id="EX-TEST01", twitch_user_id="123", user_login="viewer", channel_id="ex-test",
                direction="craft", item_id=ITEM.id, item_name=ITEM.name, count=2, status="completed",
                fee_trans_id=1, created_at=datetime.now(),
            ))
        await self.add_row()
        self.send_items.side_effect = OutOfItemsError("none")
        await self.make_cog().deliver_pending()
        rows = await self.rows(PendingExchangeDelivery)
        self.assertEqual(sorted((r.item_id, r.count, r.kind) for r in rows),
                         sorted((ing.id, n * 2, "refund") for ing, n in RECIPE.items()))
        self.assertEqual(await self.credits(), 100 + exchange.FEE)

    async def test_restart_turns_received_items_into_refunds(self):
        ing, _ = next(iter(RECIPE.items()))
        async with self.session() as session:
            session.add(ExchangeSession(
                id="EX-TEST02", twitch_user_id="123", user_login="viewer", channel_id="ex-test",
                direction="craft", item_id=ITEM.id, item_name=ITEM.name, count=1, status="waiting",
                received={ing.name: 4}, created_at=datetime.now(),
            ))
        await self.make_cog().recover_interrupted()
        (row,) = await self.rows(PendingExchangeDelivery)
        self.assertEqual((row.item_id, row.count, row.kind), (ing.id, 4, "refund"))
        (ex,) = await self.rows(ExchangeSession)
        self.assertEqual(ex.status, "interrupted")
        self.assertEqual(ex.pending, {ing.name: 4})


class LogStorageTests(DatabaseTestCase):
    async def test_only_session_lines_are_stored_in_one_batch(self):
        handler = exchange_log.ExchangeDBLogHandler()
        logger = logging.getLogger("bot.exchange.test-only")
        logger.addHandler(handler)
        try:
            log = exchange_log.SessionLogAdapter("EX-AAAAAA", "viewer", "town", logger=logger)
            log.info("first")
            log.warning("second")
            logger.info("no session")
        finally:
            logger.removeHandler(handler)
        self.assertEqual(await exchange_log.flush_exchange_logs(handler), 2)
        rows = await self.rows(ExchangeLogEntry)
        self.assertEqual([r.level for r in rows], ["INFO", "WARNING"])
        self.assertTrue(rows[0].message.startswith("EX-AAAAAA [viewer@town] first"))

    async def test_failed_write_is_retried(self):
        handler = exchange_log.ExchangeDBLogHandler()
        handler.queue.put_nowait({"session_id": "EX-1", "timestamp": datetime.now(), "level": "INFO",
                                  "message": "x", "exc_text": None})

        @asynccontextmanager
        async def broken():
            raise RuntimeError("database locked")
            yield

        with mock.patch.object(exchange_log, "get_async_session", broken):
            self.assertEqual(await exchange_log.flush_exchange_logs(handler), 0)
        self.assertEqual(handler.queue.qsize(), 1)
        self.assertEqual(await exchange_log.flush_exchange_logs(handler), 1)

    async def test_full_queue_drops_the_oldest(self):
        handler = exchange_log.ExchangeDBLogHandler(max_queued=2)
        logger = logging.getLogger("bot.exchange.test-full")
        logger.addHandler(handler)
        try:
            log = exchange_log.SessionLogAdapter("EX-BBBBBB", logger=logger)
            for i in range(3):
                log.info(f"line {i}")
        finally:
            logger.removeHandler(handler)
        self.assertEqual([e["message"].split()[-1] for e in handler.take(10)], ["1", "2"])
        self.assertEqual(handler.dropped, 1)

    async def test_purge_keeps_recent_and_owed_sessions(self):
        now = datetime.now()

        def ex(sid, days):
            return ExchangeSession(id=sid, twitch_user_id="1", user_login="u", channel_id="c", direction="craft",
                                   status="completed", created_at=now - timedelta(days=days))

        async with self.session() as session:
            session.add_all([ex("EX-OLD", 61), ex("EX-NEW", 59), ex("EX-OWED", 90)])
            await session.flush()
            for sid in ("EX-OLD", "EX-NEW", "EX-OWED"):
                session.add(ExchangeLogEntry(session_id=sid, timestamp=now, level="INFO", message=sid))
            session.add(PendingExchangeDelivery(session_id="EX-OWED", twitch_user_id="1", user_login="u",
                                                channel_id="c", item_id="i", count=1, kind="refund",
                                                created_at=now))
        self.assertEqual(await exchange_log.purge_old_exchanges(now=now), (1, 1))
        self.assertEqual(sorted(s.id for s in await self.rows(ExchangeSession)), ["EX-NEW", "EX-OWED"])
        self.assertEqual(sorted(e.session_id for e in await self.rows(ExchangeLogEntry)), ["EX-NEW", "EX-OWED"])

    async def test_session_log_text_lists_lines_in_order(self):
        now = datetime.now()
        async with self.session() as session:
            session.add(ExchangeSession(id="EX-CCCCCC", twitch_user_id="1", user_login="u", channel_id="c",
                                        direction="craft", item_name="Thing", count=1, status="completed",
                                        created_at=now))
            await session.flush()
            session.add(ExchangeLogEntry(session_id="EX-CCCCCC", timestamp=now + timedelta(seconds=1), level="INFO", message="second"))
            session.add(ExchangeLogEntry(session_id="EX-CCCCCC", timestamp=now, level="INFO", message="first"))
        cog = exchange.ExchangeCog(SimpleNamespace(dispatchers={}, global_context=SimpleNamespace()))
        text = await cog._session_log_text(["EX-CCCCCC"])
        self.assertLess(text.index("first"), text.index("second"))
        self.assertIn("status=completed", text)
        self.assertIsNone(await cog._session_log_text(["EX-NOPE"]))


class SendItemsVerificationTests(unittest.IsolatedAsyncioTestCase):
    """send_items(verify_recipient=True) settles gifts that got no reply by counting."""

    async def send(self, counts):
        town = SimpleNamespace(channel_id="v", channel_name="v", sub_event=RFChannelSubEvent.NONE)
        char_items = {"status": 200, "data": [{"user_name": "stock", "char_index": 0, "items": [
            {"id": ITEM.id, "amount": 5, "soulbound": False, "equipped": False}]}]}
        counts = list(counts)

        async def count(channel, user, item, timeout=10, log=None):
            value = counts.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        async def no_reply(channel, message, timeout=15, forward=True):
            raise redeem_rf.TimeoutError("Timed out waiting for response")

        async def never(*args, **kwargs):
            await asyncio.Event().wait()

        with (
            mock.patch.object(redeem_rf.ravenpy, "search_item", return_value=[(ITEM, 100)]),
            mock.patch.object(redeem_rf, "get_char_items", mock.AsyncMock(return_value=char_items)),
            mock.patch.object(redeem_rf, "get_sender_str", mock.AsyncMock(return_value={})),
            mock.patch.object(redeem_rf, "track_item_use", mock.AsyncMock()),
            mock.patch.object(redeem_rf, "send_ravenfall", no_reply),
            mock.patch.object(redeem_rf, "wait_for_message", never),
            mock.patch.object(redeem_rf, "query_player_item_count", count),
            mock.patch.object(redeem_rf, "VERIFY_RETRY_DELAY", 0),
            mock.patch.object(redeem_rf.RavenBotTemplates, "gift_item", return_value="gift"),
            mock.patch.object(redeem_rf.RavenBotTemplates, "query_item_count", return_value="count"),
        ):
            await redeem_rf.send_items("viewer", town, ITEM.name, 2, verify_recipient=True)

    async def test_gift_that_arrived_is_counted(self):
        await self.send([5, 7])

    async def test_gift_that_did_not_arrive_is_a_partial_send(self):
        with self.assertRaises(PartialSendError) as e:
            await self.send([5, 5])
        self.assertEqual(e.exception.items_sent, 0)

    async def test_no_answer_leaves_it_unconfirmed(self):
        with self.assertRaises(UnconfirmedSendError) as e:
            await self.send([5, None, None, None])
        self.assertEqual((e.exception.unconfirmed, e.exception.baseline, e.exception.recipient_absent), (2, 5, False))

    async def test_recipient_leaving_is_reported(self):
        with self.assertRaises(UnconfirmedSendError) as e:
            await self.send([5, RecipientNotFoundError("gone")])
        self.assertTrue(e.exception.recipient_absent)

    async def test_count_query_does_not_post_in_chat(self):
        town = SimpleNamespace(channel_id="v", channel_name="v")
        reply = RavenfallResponse(response={"Args": [4, ITEM.name]}, response_id="item_count", formatted_response="")
        send = mock.AsyncMock(return_value=reply)
        sender = {"Id": "abc", "PlatformId": "123"}
        with (
            mock.patch.object(redeem_rf, "get_sender_str", mock.AsyncMock(return_value=sender)),
            mock.patch.object(redeem_rf, "send_ravenfall", send),
        ):
            self.assertEqual(await redeem_rf.query_player_item_count(town, "viewer", ITEM), 4)
        self.assertFalse(send.await_args.kwargs["forward"])

    async def test_count_query_needs_sender_data(self):
        town = SimpleNamespace(channel_id="v", channel_name="v")
        sender = {"Id": redeem_rf.NO_SENDER_ID, "PlatformId": None}
        with mock.patch.object(redeem_rf, "get_sender_str", mock.AsyncMock(return_value=sender)):
            with self.assertRaises(RecipientNotFoundError):
                await redeem_rf.query_player_item_count(town, "viewer", ITEM)


class CommandRegistrationTests(unittest.TestCase):
    def test_longest_command_name_wins(self):
        from bot.commands.dispatchers import CommandDispatcher
        cog = exchange.ExchangeCog(SimpleNamespace(dispatchers={}, global_context=SimpleNamespace()))
        dispatcher = CommandDispatcher()
        names = set()
        for attr in ("exchange_craft", "exchange_uncraft", "exchange_cancel", "exchange_pending",
                     "exchange_log", "exchange_log_user", "exchange_info"):
            func = getattr(cog, attr)
            listener = dispatcher._func_listener(func, **func._listener_init_params)
            dispatcher.add_listener(listener)
            names.add(listener._id)
        self.assertEqual(dispatcher._find_command("exchange craft rune sword 2")[0], "exchange craft")
        self.assertEqual(dispatcher._find_command("ex uncraft rune sword")[0], "ex uncraft")
        self.assertEqual(dispatcher._find_command("exchange log user someone")[0], "exchange log user")
        self.assertEqual(dispatcher._find_command("exchange rune sword")[0], "exchange")
        params = {p.name for p in dispatcher.listeners["exchange craft"].parameters}
        self.assertEqual(params, {"item", "count", "channel"})


if __name__ == "__main__":
    unittest.main()
