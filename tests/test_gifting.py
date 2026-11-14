"""Run with: python -m unittest tests.test_gifting

Gifts wait for the game to finish preparing a dungeon, since it doesn't answer
commands until then.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from bot.cogs import redeem_rf
from bot.cogs.redeem_rf import RavenfallResponse
from bot.models import RFChannelSubEvent


def make_town(sub_event=RFChannelSubEvent.NONE):
    return SimpleNamespace(channel_id="gift-test", channel_name="town", sub_event=sub_event)


def finish_preparing_after(town, sleeps: int):
    """An asyncio.sleep that ends the dungeon preparation after a number of sleeps."""
    calls = 0

    async def sleep(_seconds):
        nonlocal calls
        calls += 1
        if calls >= sleeps:
            town.sub_event = RFChannelSubEvent.DUNGEON_READY

    return sleep


class WaitWhileDungeonPreparesTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_straight_away_without_a_dungeon_being_prepared(self):
        on_wait = mock.AsyncMock()
        waited = await redeem_rf.wait_while_dungeon_prepares(make_town(), on_wait)
        self.assertFalse(waited)
        on_wait.assert_not_awaited()

    async def test_waits_for_the_preparation_to_end(self):
        town = make_town(RFChannelSubEvent.DUNGEON_PREPARE)
        on_wait = mock.AsyncMock()
        with mock.patch("asyncio.sleep", finish_preparing_after(town, 3)):
            waited = await redeem_rf.wait_while_dungeon_prepares(town, on_wait)
        self.assertTrue(waited)
        on_wait.assert_awaited_once()
        self.assertEqual(town.sub_event, RFChannelSubEvent.DUNGEON_READY)

    async def test_gives_up_after_the_maximum_wait(self):
        town = make_town(RFChannelSubEvent.DUNGEON_PREPARE)
        with self.assertRaises(redeem_rf.TimeoutError):
            await redeem_rf.wait_while_dungeon_prepares(town, max_wait=0)

    async def test_a_failing_notice_does_not_stop_the_gift(self):
        town = make_town(RFChannelSubEvent.DUNGEON_PREPARE)
        on_wait = mock.AsyncMock(side_effect=RuntimeError("chat is down"))
        with mock.patch("asyncio.sleep", finish_preparing_after(town, 1)):
            self.assertTrue(await redeem_rf.wait_while_dungeon_prepares(town, on_wait))


class SendItemsDuringDungeonPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_items_are_gifted_once_the_dungeon_is_prepared(self):
        town = make_town(RFChannelSubEvent.DUNGEON_PREPARE)
        item = SimpleNamespace(id="item-id", name="Rune Sword")
        char_items = {"data": [
            {"user_name": "stock", "char_index": 0, "items": [
                {"id": "item-id", "amount": 5, "soulbound": False, "equipped": False},
            ]},
        ]}
        sent_during = []

        async def send_ravenfall(channel, message, timeout=15, forward=True):
            sent_during.append(channel.sub_event)
            return RavenfallResponse(response={"Args": [2]}, response_id="gift", formatted_response="")

        async def never_matches(*_args, **_kwargs):
            await asyncio.Event().wait()

        on_busy = mock.AsyncMock()
        with (
            mock.patch.object(redeem_rf.ravenpy, "search_item", return_value=[(item, 100)]),
            mock.patch.object(redeem_rf, "get_char_items", mock.AsyncMock(return_value=char_items)),
            mock.patch.object(redeem_rf, "get_sender_str", mock.AsyncMock(return_value="sender")),
            mock.patch.object(redeem_rf, "track_item_use", mock.AsyncMock()),
            mock.patch.object(redeem_rf, "send_ravenfall", send_ravenfall),
            mock.patch.object(redeem_rf, "wait_for_message", never_matches),
            mock.patch.object(redeem_rf.RavenBotTemplates, "gift_item", return_value="gift"),
            mock.patch.object(redeem_rf.RavenBotTemplates, "query_item_count", return_value="count"),
            mock.patch("asyncio.sleep", finish_preparing_after(town, 2)),
        ):
            await redeem_rf.send_items("viewer", town, "rune sword", 2, on_busy=on_busy)

        on_busy.assert_awaited_once()
        self.assertTrue(sent_during)
        self.assertNotIn(RFChannelSubEvent.DUNGEON_PREPARE, sent_during)


if __name__ == "__main__":
    unittest.main()
