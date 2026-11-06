"""Run with: python -m unittest tests.test_channel_bugfixes

The game, RavenBot and the middleman are faked. Messages to the message processor
use the exact keys that the Go middleman sends.
"""

import asyncio
import json
import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("MULTICHAT_COMMAND_SERVER_HOST", "127.0.0.1")
os.environ.setdefault("MULTICHAT_COMMAND_SERVER_PORT", "7200")
os.environ.setdefault("RF_MIDDLEMAN_HOST", "127.0.0.1")
os.environ.setdefault("RF_MIDDLEMAN_PORT", "7101")

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from bot import multichat_command, ravenfallchannel
from bot.messageprocessor import MessageMetadata, MessageProcessor
from bot.models import QueuedScroll, RFChannelSubEvent, ScrollType
from bot.ravenfallchannel import RFChannel
from bot.ravenfallmanager import RFChannelManager
from database import utils as db_utils
from database.models import Base


real_sleep = asyncio.sleep


async def fast_sleep(_seconds: float):
    await real_sleep(0.001)


def make_town() -> RFChannel:
    town = RFChannel.__new__(RFChannel)
    town.manager = SimpleNamespace(
        middleman_connected=True,
        game_updater=SimpleNamespace(holding_restarts=False, holding_scrolls=False),
        town_shutdown=SimpleNamespace(active=False),
    )
    town.channel_name = "town"
    town.channel_id = "1"
    town.middleman_connection_id = "rf_town"
    town.monitoring_paused = False
    town.is_monitoring = False
    town.current_monitor = None
    town.restart_timeout = 0.05
    town.channel_restart_lock = asyncio.Lock()
    town.channel_post_restart_lock = asyncio.Lock()
    town.ravenbot_restart_attempts = {"count": 0, "last_attempt": 0}
    town.ravenbot_prefixes = ("!",)
    town.sub_event = RFChannelSubEvent.NONE
    town.scroll_queue = deque()
    town.send_chat_message = mock.AsyncMock()
    town.restart_ravenbot = mock.AsyncMock()
    town.save_scroll_queue = mock.AsyncMock()
    town.monitor_ravenfall_command = mock.AsyncMock()
    town.first_time_joiner = mock.AsyncMock()
    town.twitch_message_waiter = SimpleNamespace(process_message=mock.AsyncMock())
    town.ravenbot_waiter = SimpleNamespace(wait_for_message=mock.AsyncMock(return_value=None))
    return town


def chat_message(text: str, first: bool = False):
    return SimpleNamespace(
        text=text,
        first=first,
        id="message-id",
        user=SimpleNamespace(id="5", name="viewer"),
    )


class ProcessorWireFormat(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.processor = MessageProcessor()
        self.seen: list[MessageMetadata] = []
        self.reply = {"message": {"Identifier": "changed"}}

        async def callback(_message, metadata, _client):
            self.seen.append(metadata)
            return self.reply

        self.processor.add_message_callback(callback)

    async def send(self):
        wrapper = {
            "source": "CLIENT",
            "clientAddr": "127.0.0.1:1",
            "serverAddr": "127.0.0.1:2",
            "connectionId": "rf_town",
            "correlationId": "rf_town_123",
            "isApi": True,
            "timestamp": "2026-11-06T00:00:00Z",
            "originalMessage": {"Identifier": "join"},
            "message": {"Identifier": "join"},
        }
        response = await self.processor.process_message(json.dumps(wrapper), mock.Mock())
        return json.loads(response)

    async def test_reads_the_middleman_keys(self):
        await self.send()
        metadata = self.seen[0]
        self.assertEqual(metadata.connection_id, "rf_town")
        self.assertEqual(metadata.correlation_id, "rf_town_123")
        self.assertTrue(metadata.is_api)

    async def test_reply_can_be_matched_by_the_middleman(self):
        response = await self.send()
        self.assertEqual(response["correlationId"], "rf_town_123")
        self.assertEqual(response["message"], {"Identifier": "changed"})

    async def test_block_can_be_matched_by_the_middleman(self):
        self.reply = {"block": True}
        response = await self.send()
        self.assertIs(response["block"], True)
        self.assertEqual(response["correlationId"], "rf_town_123")

    async def test_older_middlemen_still_work(self):
        wrapper = {"source": "CLIENT", "connection_id": "rf_town", "correlation_id": "old", "message": {"a": 1}}
        response = json.loads(await self.processor.process_message(json.dumps(wrapper), mock.Mock()))
        self.assertEqual(self.seen[0].connection_id, "rf_town")
        self.assertEqual(response["correlation_id"], "old")


class Manager(unittest.IsolatedAsyncioTestCase):
    def make_manager(self, town=None):
        manager = RFChannelManager.__new__(RFChannelManager)
        manager.channels = [town] if town else []
        manager.channel_id_to_channel = {}
        manager.started = True
        manager.middleman_connected = True
        manager.middleman_enabled = True
        manager.middleman_processor_server_client_count = 1
        return manager

    async def handle(self, out_message):
        town = SimpleNamespace(
            middleman_connection_id="rf_town",
            event_ravenbot_message=mock.AsyncMock(),
            process_ravenbot_message=mock.AsyncMock(return_value=out_message),
        )
        metadata = MessageMetadata(source="CLIENT", connection_id="rf_town")
        return await self.make_manager(town).handle_processor_message({"Identifier": "x"}, metadata, mock.Mock())

    async def test_none_blocks_the_message(self):
        self.assertEqual(await self.handle(None), {"block": True})

    async def test_block_envelope_is_not_forwarded(self):
        self.assertEqual(await self.handle({"block": True}), {"block": True})

    async def test_normal_message_is_forwarded(self):
        self.assertEqual(await self.handle({"Identifier": "y"}), {"message": {"Identifier": "y"}})

    async def test_chat_is_ignored_before_start(self):
        town = SimpleNamespace(channel_id="1", event_twitch_message=mock.AsyncMock())
        manager = self.make_manager(town)
        message = SimpleNamespace(room=SimpleNamespace(room_id="1"))
        manager.started = False
        await manager.event_twitch_message(message)
        town.event_twitch_message.assert_not_awaited()
        manager.started = True
        await manager.event_twitch_message(message)
        town.event_twitch_message.assert_awaited_once()

    async def test_disconnect_falls_back_to_chat_monitoring(self):
        manager = self.make_manager()
        await manager.on_processor_disconnect(mock.Mock())
        self.assertFalse(manager.middleman_connected)

    async def test_desync_check_survives_multichat_being_down(self):
        down = mock.AsyncMock(return_value={"status": 500, "error": "down"})
        with mock.patch("bot.ravenfallmanager.get_desync_info", down):
            self.assertEqual(await self.make_manager().get_desync_info(), {})


class ChatHandling(unittest.IsolatedAsyncioTestCase):
    async def test_bare_prefix_is_ignored(self):
        await make_town().event_twitch_message(chat_message("!", first=True))

    async def test_failed_inspect_is_not_treated_as_a_reply(self):
        town = make_town()
        failed = mock.AsyncMock(return_value={"error": "connection refused"})
        send = mock.AsyncMock()
        with (
            mock.patch.object(ravenfallchannel, "send_to_server_and_wait_response", failed),
            mock.patch.object(ravenfallchannel, "send_to_client", send),
        ):
            await town.event_twitch_message(chat_message("!inspect"))
        send.assert_not_awaited()
        town.send_chat_message.assert_not_awaited()

    async def test_inspect_is_left_to_ravenbot_without_the_middleman(self):
        town = make_town()
        town.manager.middleman_connected = False
        ask = mock.AsyncMock()
        with mock.patch.object(ravenfallchannel, "send_to_server_and_wait_response", ask):
            await town.event_twitch_message(chat_message("!inspect"))
        ask.assert_not_awaited()

    async def test_welcome_is_skipped_without_a_town_boost(self):
        town = make_town()
        town.cooldowns = mock.Mock()
        town.cooldowns.scoped.return_value.is_rate_limited.return_value = False
        town.welcome_message = "Welcome {userName}, {townSkill}"
        town.get_town_boost = mock.AsyncMock(return_value=[])
        with mock.patch("asyncio.sleep", mock.AsyncMock()):
            await RFChannel.first_time_joiner(town, chat_message("!join"))
        town.send_chat_message.assert_not_awaited()

    async def test_dungeon_loading_message_survives_the_dungeon_ending(self):
        town = make_town()
        del town.monitor_ravenfall_command
        town.sub_event = RFChannelSubEvent.DUNGEON_PREPARE
        town.dungeon = None
        with (
            mock.patch.dict(ravenfallchannel.COMMAND_TIMEOUTS, {"join": 5}),
            mock.patch("asyncio.sleep", mock.AsyncMock()),
        ):
            await town.monitor_ravenfall_command(message=chat_message("!join"))


class Watchdog(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_state_is_reset_after_an_error(self):
        town = make_town()
        town.ravenbot_waiter.wait_for_message.side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            await town._monitor_ravenbot_response_task("join")
        self.assertFalse(town.is_monitoring)
        self.assertIsNone(town.current_monitor)

    async def test_no_reply_restarts_ravenbot(self):
        town = make_town()
        with mock.patch("asyncio.sleep", mock.AsyncMock()):
            await town._monitor_ravenbot_response_task("join")
        town.restart_ravenbot.assert_awaited_once()
        self.assertEqual(town.ravenbot_restart_attempts["count"], 1)

    async def test_commands_during_a_restart_are_not_monitored(self):
        town = make_town()
        async with town.channel_restart_lock:
            await town._monitor_ravenbot_response_task("join")
        town.ravenbot_waiter.wait_for_message.assert_not_awaited()

    async def test_restart_starting_mid_wait_is_not_a_failure(self):
        town = make_town()

        async def restart_starts(**_kwargs):
            await town.channel_restart_lock.acquire()

        town.ravenbot_waiter.wait_for_message.side_effect = restart_starts
        await town._monitor_ravenbot_response_task("join")
        town.restart_ravenbot.assert_not_awaited()
        self.assertEqual(town.ravenbot_restart_attempts["count"], 0)


class PostRestart(unittest.IsolatedAsyncioTestCase):
    async def run_post_restart(self, town):
        ok = mock.AsyncMock(return_value={"status": 200})
        with (
            mock.patch.object(ravenfallchannel, "send_multichat_command", ok),
            mock.patch("asyncio.sleep", fast_sleep),
        ):
            await town._ravenfall_post_restart()
        return ok

    async def test_game_being_unreachable_does_not_crash_it(self):
        town = make_town()
        town.manager.middleman_connected = False
        town.get_query = mock.AsyncMock(
            side_effect=[None, {"players": 1}, None, {"players": 2}, {"players": 2}]
        )
        ok = await self.run_post_restart(town)
        ok.assert_awaited_once()

    async def test_players_that_never_settle_do_not_hold_it_forever(self):
        town = make_town()
        town.manager.middleman_connected = False
        counts = iter(range(1, 100000))
        town.get_query = mock.AsyncMock(side_effect=lambda *_a, **_k: {"players": next(counts)})
        ok = await asyncio.wait_for(self.run_post_restart(town), 5)
        ok.assert_awaited_once()


class ScrollQueue(unittest.IsolatedAsyncioTestCase):
    def make_town(self, entry: QueuedScroll) -> RFChannel:
        town = make_town()
        town.update_events_routine_first_iteration = False
        town.restart_task = None
        town.event = ravenfallchannel.RFChannelEvent.NONE
        town.scroll_queue_failures = 0
        town.twitch = mock.Mock(update_redemption_status=mock.AsyncMock())
        town.scroll_queue.append(entry)
        return town

    def counts(self, amount: int):
        return mock.AsyncMock(return_value={
            "status": 200,
            "data": {"channel": {"Raid Scroll": amount, "Dungeon Scroll": amount}},
        })

    async def tick(self, town, counts):
        with mock.patch.object(ravenfallchannel, "get_scroll_counts", counts):
            await town.scroll_queue_routine._coro(town)

    async def test_out_of_stock_entry_is_removed_and_refunded(self):
        town = self.make_town(QueuedScroll(ScrollType.RAID, None, None, "5", 12))
        add_credits = mock.AsyncMock()
        with (
            mock.patch.object(ravenfallchannel.db_utils, "add_credits", add_credits),
            mock.patch.object(ravenfallchannel, "get_async_session", mock.MagicMock()),
        ):
            await self.tick(town, self.counts(0))
        self.assertEqual(len(town.scroll_queue), 0)
        self.assertEqual(add_credits.await_args.args[1:3], ("5", 12))
        town.save_scroll_queue.assert_awaited()
        self.assertIn("refunded", town.send_chat_message.await_args.args[0])

    async def test_failed_stock_check_keeps_the_queue(self):
        town = self.make_town(QueuedScroll(ScrollType.RAID, None, None, "5", 12))
        await self.tick(town, mock.AsyncMock(return_value={"status": 500, "error": "down"}))
        self.assertEqual(len(town.scroll_queue), 1)


class GameQueries(unittest.IsolatedAsyncioTestCase):
    async def test_unreadable_reply_counts_as_a_failed_query(self):
        response = mock.MagicMock()
        response.__aenter__ = mock.AsyncMock(return_value=response)
        response.__aexit__ = mock.AsyncMock(return_value=False)
        response.json = mock.AsyncMock(side_effect=ValueError("not json"))
        session = mock.MagicMock()
        session.__aenter__ = mock.AsyncMock(return_value=session)
        session.__aexit__ = mock.AsyncMock(return_value=False)
        session.get.return_value = response
        town = make_town()
        town.rf_query_url = "http://game"
        with mock.patch.object(ravenfallchannel.aiohttp, "ClientSession", return_value=session):
            self.assertIsNone(await town.get_query("select * from session"))


class CoinTracking(unittest.IsolatedAsyncioTestCase):
    async def test_coins_use_their_own_endpoint(self):
        response = mock.MagicMock(status=200)
        response.__aenter__ = mock.AsyncMock(return_value=response)
        response.__aexit__ = mock.AsyncMock(return_value=False)
        response.json = mock.AsyncMock(return_value={})
        session = mock.MagicMock()
        session.__aenter__ = mock.AsyncMock(return_value=session)
        session.__aexit__ = mock.AsyncMock(return_value=False)
        session.post.return_value = response
        with mock.patch.object(multichat_command.aiohttp, "ClientSession", return_value=session):
            await multichat_command.track_coin_use("viewer", 100)
        self.assertTrue(session.post.call_args.args[0].endswith("/track_coin_use"))


class Credits(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.folder.name) / 'test.db'}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.folder.cleanup()

    async def test_sessions_loaded_together_keep_every_change(self):
        async with AsyncSession(self.engine) as session:
            await db_utils.add_credits(session, 5, 100, "start")
            await session.commit()

        first = AsyncSession(self.engine)
        second = AsyncSession(self.engine)
        try:
            first_row = await db_utils.get_user_credits_raw(first, 5)
            second_row = await db_utils.get_user_credits_raw(second, 5)
            self.assertEqual((first_row.credits, second_row.credits), (100, 100))
            await db_utils.add_credits(first, 5, -30, "first")
            await first.commit()
            await db_utils.add_credits(second, 5, -30, "second")
            await second.commit()
        finally:
            await first.close()
            await second.close()

        async with AsyncSession(self.engine) as session:
            self.assertEqual(await db_utils.get_user_credits(session, 5), 40)


if __name__ == "__main__":
    unittest.main()
