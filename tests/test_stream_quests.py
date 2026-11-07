"""Run with: python -m unittest tests.test_stream_quests

The offers reply is written the way the game builds it, from `MSG_STREAM_QUEST_OFFERS`
and `UI_STREAM_QUEST_OFFER_ENTRY`.
"""

import json
import unittest
from collections import deque
from types import SimpleNamespace
from unittest import mock

from bot import ravenfallchannel
from bot.message_templates import RavenBotTemplates
from bot.models import QueuedScroll, ScrollType
from bot.ravenfallchannel import RFChannel
from bot.streamquests import (
    TownActivity,
    completion_ratio,
    is_working,
    levels_per_minute,
    parse_offer,
    parse_offers,
    pick_offer,
)
from ravenpy import experience_for_level

OFFERS_FORMAT = "No stream quest running. Today's: {offers}. The streamer or a mod starts one with !quest stream start 1."
RUNNING_FORMAT = "Stream quest: {quest} ({progress}/{amount}), {minutes} minutes left, {helpers} helping so far. Reward: {reward}."


def offers_reply(text: str) -> dict:
    return {"Format": OFFERS_FORMAT, "Args": [text]}


def player(training: str = "None", name: str = "a", **state) -> dict:
    return {"id": name, "training": training, "island": "Home", **state}


class ParseOffers(unittest.TestCase):
    def test_reads_every_offer(self):
        offers = parse_offers(offers_reply(
            "1. Chop 1,200 wood (60 min), 2. Win 3 raids (120 min), 3. Gain 40 levels (120 min)"
        ))
        self.assertEqual(
            [(o.slot, o.objective, o.amount, o.minutes, o.skill) for o in offers],
            [
                (1, "gather", 1200, 60, "woodcutting"),
                (2, "raid", 3, 120, None),
                (3, "levels", 40, 120, None),
            ],
        )

    def test_every_wording_the_game_has(self):
        for text, objective, amount in (
            ("Chop 300 wood", "gather", 300),
            ("Mine 300 ore", "gather", 300),
            ("Catch 300 fish", "gather", 300),
            ("Harvest 300 wheat", "gather", 300),
            ("Gather 300 times", "gather", 300),
            ("Defeat 300 enemies", "kill", 300),
            ("Craft, cook or brew 30 items", "make", 30),
            ("Gain 10 levels", "levels", 10),
            ("Win a raid", "raid", 1),
            ("Win 3 raids", "raid", 3),
            ("Clear a dungeon", "dungeon", 1),
            ("Clear 2 dungeons", "dungeon", 2),
            ("Hold 10 waves on Ravebridge in hero mode", "hero_wave", 10),
        ):
            offer = parse_offer(1, text, 60)
            self.assertEqual((offer.objective, offer.amount), (objective, amount), text)

    def test_a_comma_in_the_wording_does_not_split_the_offer(self):
        offers = parse_offers(offers_reply("1. Craft, cook or brew 30 items (60 min), 2. Clear 2 dungeons (120 min)"))
        self.assertEqual([o.objective for o in offers], ["make", "dungeon"])

    def test_a_running_quest_has_no_offers(self):
        self.assertEqual(parse_offers({"Format": RUNNING_FORMAT, "Args": ["Chop 300 wood", "5", "300"]}), [])

    def test_no_offers_left(self):
        self.assertEqual(parse_offers(offers_reply("none left today")), [])

    def test_unknown_wording_is_never_picked(self):
        offers = parse_offers(offers_reply("1. Pet 5 cats (60 min)"))
        self.assertIsNone(offers[0].objective)
        self.assertIsNone(pick_offer(offers, TownActivity(players=[player("Attack")])))


class Estimates(unittest.TestCase):
    def test_gathering_counts_the_players_on_that_skill(self):
        offer = parse_offer(1, "Chop 720 wood", 60)
        town = TownActivity(players=[player("Woodcutting"), player("Fishing")])
        self.assertEqual(completion_ratio(offer, town), 0.5)

    def test_kills_count_every_fighter(self):
        offer = parse_offer(1, "Defeat 1,200 enemies", 60)
        town = TownActivity(players=[player("Melee"), player("Ranged"), player("Woodcutting")])
        self.assertEqual(completion_ratio(offer, town), 1)

    def test_players_away_from_their_training_do_not_count(self):
        self.assertTrue(is_working(player("Mining")))
        self.assertFalse(is_working(player()))
        self.assertFalse(is_working(player("Mining", island="None")))
        for state in ("sailing", "resting", "inarena", "induel", "indungeon", "inraid"):
            self.assertFalse(is_working(player("Mining", **{state: True})), state)

    def test_levels_use_the_measured_rate(self):
        offer = parse_offer(1, "Gain 12 levels", 120)
        self.assertEqual(completion_ratio(offer, TownActivity(players=[player()], levels_per_minute=0.2)), 2)

    def test_queued_scrolls_count_towards_events(self):
        offer = parse_offer(1, "Win 3 raids", 120)
        without = completion_ratio(offer, TownActivity(players=[player()]))
        with_scrolls = completion_ratio(offer, TownActivity(players=[player()], queued_raids=3))
        self.assertAlmostEqual(with_scrolls - without, 1)

    def test_items_and_hero_waves_never_progress_by_themselves(self):
        town = TownActivity(players=[player("Crafting"), player("Cooking")])
        self.assertEqual(completion_ratio(parse_offer(1, "Craft, cook or brew 30 items", 60), town), 0)
        self.assertEqual(completion_ratio(parse_offer(1, "Hold 10 waves on Ravebridge in hero mode", 120), town), 0)

    def test_an_empty_town_progresses_on_nothing(self):
        self.assertEqual(completion_ratio(parse_offer(1, "Win a raid", 120), TownActivity(players=[])), 0)


class Picking(unittest.TestCase):
    def test_picks_the_highest_share_of_its_target(self):
        offers = [
            parse_offer(1, "Chop 720 wood", 60),
            parse_offer(2, "Catch 300 fish", 60),
            parse_offer(3, "Win 3 raids", 120),
        ]
        town = TownActivity(players=[player("Fishing"), player("Attack")])
        self.assertEqual(pick_offer(offers, town).slot, 2)

    def test_picks_nothing_when_no_offer_can_progress(self):
        offers = [parse_offer(1, "Chop 300 wood", 60), parse_offer(2, "Craft, cook or brew 30 items", 60)]
        self.assertIsNone(pick_offer(offers, TownActivity(players=[player("Fishing")])))

    def test_picks_nothing_when_no_offer_would_finish_in_time(self):
        offers = [parse_offer(1, "Catch 361 fish", 60), parse_offer(2, "Win 3 raids", 120)]
        self.assertIsNone(pick_offer(offers, TownActivity(players=[player("Fishing")])))

    def test_an_offer_that_finishes_just_in_time_is_picked(self):
        offer = parse_offer(1, "Catch 360 fish", 60)
        self.assertEqual(pick_offer([offer], TownActivity(players=[player("Fishing")])), offer)


class LevelRate(unittest.TestCase):
    def stats(self, level: int, experience: float) -> dict:
        return {"combatlevel": 5, "mining": {"level": level, "experience": experience, "maxlevel": level, "currentvalue": level}}

    def test_a_tenth_of_a_level_in_a_minute(self):
        needed = experience_for_level(51)
        before = [{"id": "a", "stats": self.stats(50, 0)}]
        after = [{"id": "a", "stats": self.stats(50, needed / 10)}]
        self.assertAlmostEqual(levels_per_minute(before, after, 60), 0.1)

    def test_a_level_up_between_the_samples(self):
        before = [{"id": "a", "stats": self.stats(50, experience_for_level(51) * 0.9)}]
        after = [{"id": "a", "stats": self.stats(51, experience_for_level(52) * 0.1)}]
        self.assertAlmostEqual(levels_per_minute(before, after, 60), 0.2)

    def test_players_who_joined_or_left_are_ignored(self):
        before = [{"id": "a", "stats": self.stats(50, 0)}]
        after = [{"id": "b", "stats": self.stats(60, 0)}]
        self.assertEqual(levels_per_minute(before, after, 60), 0)

    def test_the_top_level_does_not_crash(self):
        before = [{"id": "a", "stats": self.stats(999, 5)}]
        self.assertEqual(levels_per_minute(before, before, 60), 0)


class QuestRoutine(unittest.IsolatedAsyncioTestCase):
    def make_town(self, players: list[dict]) -> RFChannel:
        town = RFChannel.__new__(RFChannel)
        town.channel_name = "town"
        town.channel_id = "1"
        town.middleman_connection_id = "conn"
        town.auto_stream_quests = True
        town.manager = SimpleNamespace(middleman_connected=True, middleman_enabled=False)
        town.channel_restart_lock = mock.Mock(locked=mock.Mock(return_value=False))
        town.restart_task = None
        town.next_stream_quest_attempt = 0
        town.ravenbot_prefixes = ("!",)
        town.scroll_queue = deque()
        town.send_chat_message = mock.AsyncMock()
        town.get_players = mock.AsyncMock(return_value=players)
        return town

    async def tick(self, town: RFChannel, reply: dict):
        self.send = mock.AsyncMock(return_value=reply)
        with (
            mock.patch.object(ravenfallchannel, "send_to_server_and_wait_response", self.send),
            mock.patch.object(ravenfallchannel.asyncio, "sleep", mock.AsyncMock()),
        ):
            await town.stream_quest_routine._coro(town)

    def offers(self, text: str) -> dict:
        return {"success": True, "responses": [offers_reply(text)]}

    async def test_starts_the_best_offer_in_chat(self):
        town = self.make_town([player("Fishing")])
        await self.tick(town, self.offers("1. Chop 300 wood (60 min), 2. Catch 300 fish (60 min)"))
        town.send_chat_message.assert_awaited_once_with("!quest stream start 2")
        query = json.loads(self.send.await_args.args[1])
        self.assertEqual(query["Identifier"], "quest_command")
        self.assertEqual(json.loads(query["Content"]), "stream")

    async def test_nothing_is_said_while_a_quest_runs(self):
        town = self.make_town([player("Fishing")])
        await self.tick(town, {"success": True, "responses": [{"Format": RUNNING_FORMAT, "Args": []}]})
        town.send_chat_message.assert_not_awaited()

    async def test_nothing_is_said_when_the_game_does_not_answer(self):
        town = self.make_town([player("Fishing")])
        await self.tick(town, {"success": True, "timeout": True, "responses": []})
        town.send_chat_message.assert_not_awaited()

    async def test_a_refused_start_is_not_repeated_on_the_next_tick(self):
        town = self.make_town([player("Fishing")])
        reply = self.offers("1. Catch 300 fish (60 min)")
        await self.tick(town, reply)
        await self.tick(town, reply)
        town.send_chat_message.assert_awaited_once()

    async def test_measures_levels_only_when_offered(self):
        town = self.make_town([player("Fishing")])
        await self.tick(town, self.offers("1. Catch 300 fish (60 min)"))
        self.assertEqual(town.get_players.await_count, 1)

        town = self.make_town([player("Fishing")])
        await self.tick(town, self.offers("1. Catch 300 fish (60 min), 2. Gain 10 levels (120 min)"))
        self.assertEqual(town.get_players.await_count, 3)
        town.send_chat_message.assert_awaited_once_with("!quest stream start 1")

    async def test_queued_scrolls_reach_the_estimate(self):
        town = self.make_town([player()])
        town.scroll_queue.extend(QueuedScroll(ScrollType.RAID, None, None, "5", 1) for _ in range(5))
        await self.tick(town, self.offers("1. Clear 2 dungeons (120 min), 2. Win 3 raids (120 min)"))
        town.send_chat_message.assert_awaited_once_with("!quest stream start 2")

    async def test_skips_a_town_that_is_turned_off_or_restarting(self):
        town = self.make_town([player("Fishing")])
        town.auto_stream_quests = False
        await self.tick(town, self.offers("1. Catch 300 fish (60 min)"))
        self.send.assert_not_awaited()

        town = self.make_town([player("Fishing")])
        town.channel_restart_lock.locked.return_value = True
        await self.tick(town, self.offers("1. Catch 300 fish (60 min)"))
        self.send.assert_not_awaited()

        town = self.make_town([player("Fishing")])
        town.restart_task = mock.Mock(finished=mock.Mock(return_value=False))
        await self.tick(town, self.offers("1. Catch 300 fish (60 min)"))
        self.send.assert_not_awaited()


class Template(unittest.TestCase):
    def test_correlation_id_is_kept_for_the_reply(self):
        message = json.loads(RavenBotTemplates.stream_quest_status({"Username": "town"}, correlation_id="abc"))
        self.assertEqual(message["CorrelationId"], "abc")


if __name__ == "__main__":
    unittest.main()
