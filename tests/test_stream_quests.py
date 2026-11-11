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
from bot.cogs.game import GameCog
from bot.cogs.info import InfoCog
from bot.message_templates import RavenBotTemplates
from bot.ravenfallchannel import RFChannel
from bot.models import QueuedScroll, ScrollType
from bot.streamquests import (
    RunningQuest,
    TownActivity,
    completion_ratio,
    faster_offer,
    format_quest_list,
    is_working,
    natural_wait_minutes,
    parse_offer,
    parse_offers,
    parse_running,
    pick_offer,
)
from tests.shared_loc import shared_localization

OFFERS_FORMAT = "No stream quest running. Today's: {offers}. The streamer or a mod starts one with !quest stream start 1."
RUNNING_FORMAT = "Stream quest: {quest} ({progress}/{amount}), {minutes} minutes left, {helpers} helping so far. Reward: {reward}."


def offers_reply(text: str) -> dict:
    return {"Format": OFFERS_FORMAT, "Args": [text]}


def running_reply(text: str = "Win 3 raids", progress: str = "0", amount: str = "3") -> dict:
    return {"Format": RUNNING_FORMAT, "Args": [text, progress, amount, 100, 5, "some coins"]}


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

    def test_a_running_quest_is_read_from_its_status(self):
        self.assertEqual(
            parse_running(running_reply("Defeat 1,200 enemies", "1,050", "1,200")),
            RunningQuest("Defeat 1,200 enemies", "kill", 1200, 1050, 100),
        )

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

    def added_by_scrolls(self, text: str, **activity) -> float:
        offer = parse_offer(1, text, 120)
        without = completion_ratio(offer, TownActivity(players=[player()]))
        return (completion_ratio(offer, TownActivity(players=[player()], **activity)) - without) * offer.amount

    def test_queued_scrolls_count_towards_events(self):
        self.assertAlmostEqual(self.added_by_scrolls("Win 3 raids", raid_scrolls=3, raids_queued=3), 3)

    def test_scrolls_nobody_uses_count_for_nothing(self):
        self.assertAlmostEqual(self.added_by_scrolls("Win 3 raids", raid_scrolls=50), 0)

    def test_the_rest_of_the_stock_goes_at_the_recent_pace(self):
        self.assertAlmostEqual(self.added_by_scrolls("Win 9 raids", raid_scrolls=50, scroll_uses=1), 8)

    def test_the_stock_caps_the_pace(self):
        self.assertAlmostEqual(self.added_by_scrolls("Win 3 raids", raid_scrolls=2, raids_queued=1, scroll_uses=9), 2)

    def test_queued_scrolls_beyond_the_stock_do_not_count(self):
        self.assertAlmostEqual(self.added_by_scrolls("Win 3 raids", raid_scrolls=1, raids_queued=3), 1)

    def test_each_kind_uses_its_own_stock(self):
        self.assertAlmostEqual(self.added_by_scrolls("Clear 2 dungeons", raid_scrolls=5, scroll_uses=9), 0)
        self.assertAlmostEqual(self.added_by_scrolls("Clear 2 dungeons", dungeon_scrolls=2, scroll_uses=9), 2)

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


    def test_natural_events_are_a_last_resort(self):
        raid = parse_offer(1, "Win a raid", 120)
        fish = parse_offer(2, "Catch 300 fish", 60)
        town = TownActivity(players=[player("Fishing")])
        self.assertGreater(completion_ratio(raid, town), completion_ratio(fish, town))
        self.assertEqual(pick_offer([raid, fish], town), fish)

    def test_natural_events_are_picked_when_nothing_else_would_finish(self):
        raid = parse_offer(1, "Win a raid", 120)
        fish = parse_offer(2, "Catch 900 fish", 60)
        self.assertEqual(pick_offer([raid, fish], TownActivity(players=[player("Fishing")])), raid)

    def test_recent_uses_or_a_queued_scroll_rank_it_normally(self):
        raid = parse_offer(1, "Win a raid", 120)
        fish = parse_offer(2, "Catch 300 fish", 60)
        fishers = [player("Fishing")]
        self.assertEqual(pick_offer([raid, fish], TownActivity(players=fishers, scroll_uses=1)), raid)
        self.assertEqual(pick_offer([raid, fish], TownActivity(players=fishers, raid_scrolls=1, raids_queued=1)), raid)


class FasterOffer(unittest.TestCase):
    QUEST = RunningQuest("Win 2 raids", "raid", 2, 0)

    def test_the_wait_counts_only_what_is_left(self):
        self.assertAlmostEqual(natural_wait_minutes(self.QUEST), 2 * 4750 / 60)
        self.assertAlmostEqual(natural_wait_minutes(RunningQuest("Clear 2 dungeons", "dungeon", 2, 1)), 5250 / 60)

    def test_an_offer_that_finishes_sooner_wins(self):
        offer = parse_offer(1, "Catch 300 fish", 60)
        self.assertEqual(faster_offer([offer], self.QUEST, TownActivity(players=[player("Fishing")])), offer)

    def test_the_soonest_of_several_wins(self):
        slow = parse_offer(1, "Catch 300 fish", 60)
        fast = parse_offer(2, "Chop 60 wood", 60)
        town = TownActivity(players=[player("Fishing"), player("Woodcutting")])
        self.assertEqual(faster_offer([slow, fast], self.QUEST, town), fast)

    def test_nothing_finishable_keeps_the_quest(self):
        offer = parse_offer(1, "Catch 900 fish", 60)
        self.assertIsNone(faster_offer([offer], self.QUEST, TownActivity(players=[player("Fishing")])))

    def test_an_offer_slower_than_the_wait_keeps_the_quest(self):
        quest = RunningQuest("Win 2 raids", "raid", 2, 1)
        offer = parse_offer(1, "Catch 700 fish", 120)
        town = TownActivity(players=[player("Fishing")])
        self.assertEqual(pick_offer([offer], town), offer)
        self.assertIsNone(faster_offer([offer], quest, town))

    def test_the_same_kind_or_natural_events_are_not_an_alternative(self):
        offers = [parse_offer(1, "Win a raid", 120), parse_offer(2, "Clear a dungeon", 120)]
        self.assertIsNone(faster_offer(offers, RunningQuest("Win 3 raids", "raid", 3, 0), TownActivity(players=[player()])))


class LevelRate(unittest.IsolatedAsyncioTestCase):
    async def rate(self, result=None, error=None) -> float | None:
        town = RFChannel.__new__(RFChannel)
        town.channel_name = "town"
        query = mock.AsyncMock(return_value=result, side_effect=error)
        with mock.patch.object(ravenfallchannel, "get_prometheus_instant", query):
            rate = await town.measure_levels_per_minute()
        self.query = query.await_args.args[0]
        return rate

    async def test_levels_over_the_last_hour_become_a_rate_per_minute(self):
        rate = await self.rate([{"metric": {}, "value": [1, "12"]}])
        self.assertAlmostEqual(rate, 0.2)
        self.assertIn('session="town"', self.query)
        self.assertIn("[3600s]", self.query)

    async def test_no_history_is_not_a_rate_of_zero(self):
        self.assertIsNone(await self.rate([]))

    async def test_prometheus_being_down_is_not_a_rate_of_zero(self):
        self.assertIsNone(await self.rate(error=OSError("down")))


class RoutineTestCase(unittest.IsolatedAsyncioTestCase):
    def make_town(self, players: list[dict]) -> RFChannel:
        town = RFChannel.__new__(RFChannel)
        town.channel_name = "town"
        town.channel_id = "1"
        town.middleman_connection_id = "conn"
        town.auto_stream_quests = True
        town.manager = SimpleNamespace(middleman_connected=True, middleman_enabled=False)
        town.channel_restart_lock = mock.Mock(locked=mock.Mock(return_value=False))
        town.restart_task = None
        town.next_stream_quest_check = 0
        town.rfloc = shared_localization()
        town.scroll_uses = deque()
        town.saved_stream_quest_offers = []
        town.saved_stream_quest_offers_day = None
        town.watched_stream_quest = None
        town.watched_stream_quest_answered_at = 0
        town.ravenbot_prefixes = ("!",)
        town.scroll_queue = deque()
        town.send_chat_message = mock.AsyncMock()
        town.get_players = mock.AsyncMock(return_value=players)
        return town

    def setUp(self):
        self.prometheus = mock.AsyncMock(return_value=[{"metric": {}, "value": [1, "0"]}])

    def scroll_counts(self, raid: int = 0, dungeon: int = 0):
        data = {"channel": {"Raid Scroll": raid, "Dungeon Scroll": dungeon}}
        return mock.AsyncMock(return_value={"status": 200, "data": data})

    async def tick(self, town: RFChannel, reply: dict, scrolls=None):
        self.send = mock.AsyncMock(return_value=reply)
        with (
            mock.patch.object(ravenfallchannel, "get_scroll_counts", scrolls or self.scroll_counts()),
            mock.patch.object(ravenfallchannel, "get_prometheus_instant", self.prometheus),
            mock.patch.object(ravenfallchannel, "send_to_server_and_wait_response", self.send),
            mock.patch.object(ravenfallchannel.asyncio, "sleep", mock.AsyncMock()),
        ):
            town.next_stream_quest_check = 0
            await town.stream_quest_routine._coro(town)

    def offers(self, text: str) -> dict:
        return {"success": True, "responses": [offers_reply(text)]}


class QuestRoutine(RoutineTestCase):
    async def test_starts_the_best_offer_in_chat(self):
        town = self.make_town([player("Fishing")])
        await self.tick(town, self.offers("1. Chop 300 wood (60 min), 2. Catch 300 fish (60 min)"))
        town.send_chat_message.assert_awaited_once_with("!quest stream start 2")
        query = json.loads(self.send.await_args.args[1])
        self.assertEqual(query["Identifier"], "quest_command")
        self.assertEqual(json.loads(query["Content"]), "stream")

    async def test_nothing_is_said_while_a_quest_runs(self):
        town = self.make_town([player("Fishing")])
        await self.tick(town, {"success": True, "responses": [running_reply()]})
        town.send_chat_message.assert_not_awaited()

    async def test_nothing_is_said_when_the_game_does_not_answer(self):
        town = self.make_town([player("Fishing")])
        await self.tick(town, {"success": True, "timeout": True, "responses": []})
        town.send_chat_message.assert_not_awaited()

    async def test_a_refused_start_is_tried_again_on_the_next_check(self):
        town = self.make_town([player("Fishing")])
        reply = self.offers("1. Catch 300 fish (60 min)")
        await self.tick(town, reply)
        await self.tick(town, reply)
        self.assertEqual(town.send_chat_message.await_count, 2)

    async def test_waits_for_the_next_check(self):
        town = self.make_town([player("Fishing")])
        town.next_stream_quest_check = ravenfallchannel.time.time() + 30
        town.check_stream_quest = mock.AsyncMock()
        await town.stream_quest_routine._coro(town)
        town.check_stream_quest.assert_not_awaited()

    async def test_a_check_books_the_next_one_a_minute_later(self):
        town = self.make_town([player("Fishing")])
        town.check_stream_quest = mock.AsyncMock()
        await town.stream_quest_routine._coro(town)
        town.check_stream_quest.assert_awaited_once()
        left = town.next_stream_quest_check - ravenfallchannel.time.time()
        self.assertAlmostEqual(left, 60, delta=5)

    async def test_measures_levels_only_when_offered(self):
        town = self.make_town([player("Fishing")])
        await self.tick(town, self.offers("1. Catch 300 fish (60 min)"))
        self.prometheus.assert_not_awaited()

        town = self.make_town([player("Fishing")])
        await self.tick(town, self.offers("1. Catch 300 fish (60 min), 2. Gain 10 levels (120 min)"))
        self.prometheus.assert_awaited_once()
        town.send_chat_message.assert_awaited_once_with("!quest stream start 1")

    async def test_scrolls_in_stock_reach_the_estimate(self):
        town = self.make_town([player()])
        town.scroll_uses.append(ravenfallchannel.time.time())
        await self.tick(
            town,
            self.offers("1. Clear 2 dungeons (120 min), 2. Win 3 raids (120 min)"),
            self.scroll_counts(raid=5),
        )
        town.send_chat_message.assert_awaited_once_with("!quest stream start 2")

    async def test_old_uses_and_the_queue_reach_the_estimate(self):
        town = self.make_town([player()])
        now = ravenfallchannel.time.time()
        town.scroll_uses.extend([now - 16 * 60, now - 60, now - 30])
        town.scroll_queue.append(QueuedScroll(ScrollType.DUNGEON, None, None, "1", 0))
        self.assertEqual(town.count_scroll_uses(), 2)
        await self.tick(
            town,
            self.offers("1. Clear 2 dungeons (120 min)"),
            self.scroll_counts(dungeon=1),
        )
        town.send_chat_message.assert_awaited_once_with("!quest stream start 1")

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
        town.restart_task = self.restart_in(60)
        await self.tick(town, self.offers("1. Catch 300 fish (60 min)"))
        self.send.assert_not_awaited()

    async def test_a_scheduled_restart_days_away_does_not_stop_it(self):
        town = self.make_town([player("Fishing")])
        town.restart_task = self.restart_in(3 * 24 * 60 * 60)
        await self.tick(town, self.offers("1. Catch 300 fish (60 min)"))
        town.send_chat_message.assert_awaited_once_with("!quest stream start 1")

    def restart_in(self, seconds: int):
        return mock.Mock(
            finished=mock.Mock(return_value=False),
            get_time_left=mock.Mock(return_value=seconds),
        )


class UnansweredQuest(RoutineTestCase):
    FISH = "1. Catch 300 fish (60 min), 2. Win 3 raids (120 min)"

    async def town_with_saved_offers(self, offers: str = FISH) -> RFChannel:
        """A town that saved its offers, then started quest 2 sixteen minutes ago."""
        town = self.make_town([player("Fishing")])
        town.scroll_uses.append(ravenfallchannel.time.time())
        await self.tick(town, self.offers(offers))
        town.send_chat_message.reset_mock()
        town.scroll_uses.clear()
        await self.check(town)
        town.watched_stream_quest_answered_at -= 16 * 60
        return town

    async def check(self, town: RFChannel, reply: dict | None = None):
        await self.tick(town, {"success": True, "responses": [reply or running_reply()]})

    async def test_stops_for_an_offer_that_is_faster(self):
        town = await self.town_with_saved_offers()
        await self.check(town)
        town.send_chat_message.assert_awaited_once_with("!quest stream stop")

    async def test_keeps_the_quest_when_nothing_is_faster(self):
        town = await self.town_with_saved_offers("1. Catch 900 fish (60 min), 2. Win 3 raids (120 min)")
        await self.check(town)
        town.send_chat_message.assert_not_awaited()

    async def test_the_quest_gets_fifteen_minutes_first(self):
        town = await self.town_with_saved_offers()
        town.watched_stream_quest_answered_at += 2 * 60
        await self.check(town)
        town.send_chat_message.assert_not_awaited()

    async def test_a_scroll_use_answers_it(self):
        town = await self.town_with_saved_offers()
        town.scroll_uses.append(ravenfallchannel.time.time() - 60)
        await self.check(town)
        town.send_chat_message.assert_not_awaited()

    async def test_progress_answers_it(self):
        town = await self.town_with_saved_offers()
        await self.check(town, running_reply(progress="1"))
        town.send_chat_message.assert_not_awaited()

    async def test_a_different_quest_starts_its_own_wait(self):
        town = await self.town_with_saved_offers()
        await self.check(town, running_reply("Clear a dungeon", amount="1"))
        town.send_chat_message.assert_not_awaited()

    async def test_other_quests_are_left_alone(self):
        town = await self.town_with_saved_offers()
        await self.check(town, running_reply("Chop 300 wood", amount="300"))
        town.send_chat_message.assert_not_awaited()
        self.assertIsNone(town.watched_stream_quest)

    async def test_no_saved_offers_keeps_the_quest(self):
        town = await self.town_with_saved_offers()
        town.saved_stream_quest_offers = []
        await self.check(town)
        town.send_chat_message.assert_not_awaited()

    async def test_yesterdays_offers_keep_the_quest(self):
        town = await self.town_with_saved_offers()
        town.saved_stream_quest_offers_day = ravenfallchannel.datetime(2000, 1, 1).date()
        await self.check(town)
        town.send_chat_message.assert_not_awaited()

    async def test_the_log_says_why_it_stayed_or_stopped(self):
        town = await self.town_with_saved_offers("1. Catch 900 fish (60 min), 2. Win 3 raids (120 min)")
        with self.assertLogs("bot.streamquests", level="INFO") as logs:
            await self.check(town)
        self.assertIn("kept the quest, Win 3 raids needs about 238 min more", "\n".join(logs.output))

        town = await self.town_with_saved_offers()
        with self.assertLogs("bot.streamquests", level="INFO") as logs:
            await self.check(town)
        self.assertIn("stopping the quest, Win 3 raids needs about 238 min more, 1 is faster", "\n".join(logs.output))


class QuestLog(RoutineTestCase):
    """Every check says what it decided in the log."""

    LOGGER = "bot.streamquests"

    async def logged(self, town: RFChannel, reply: dict) -> str:
        with self.assertLogs(self.LOGGER, level="INFO") as logs:
            await self.tick(town, reply)
        return "\n".join(logs.output)

    async def test_a_start_shows_each_offer_and_its_share(self):
        town = self.make_town([player("Fishing")])
        text = await self.logged(town, self.offers("1. Chop 300 wood (60 min), 2. Catch 300 fish (60 min)"))
        self.assertIn("town: starting 2: 1. Chop 300 wood (0%), 2. Catch 300 fish (120%)", text)

    async def test_no_finishable_offer_shows_the_shares(self):
        town = self.make_town([player("Fishing")])
        text = await self.logged(town, self.offers("1. Win 3 raids (120 min)"))
        self.assertIn("no offer should finish in time: 1. Win 3 raids (51%)", text)

    async def test_a_running_quest_is_quoted(self):
        town = self.make_town([player()])
        reply = {"success": True, "responses": [running_reply("Chop 300 wood", amount="300")]}
        self.assertIn("Chop 300 wood", await self.logged(town, reply))

    async def test_no_reply_is_logged(self):
        town = self.make_town([player()])
        text = await self.logged(town, {"success": True, "timeout": True, "responses": []})
        self.assertIn("no reply from the game", text)

    async def test_a_muted_town_names_its_connection(self):
        town = self.make_town([player()])
        town.manager.middleman_enabled = True
        town.middleman_connection_status = SimpleNamespace(server_connected=False)
        text = await self.logged(town, self.offers("1. Catch 300 fish (60 min)"))
        self.assertIn("connection conn has no game connected", text)
        self.send.assert_not_awaited()

    async def test_each_other_skip_has_a_reason(self):
        town = self.make_town([player()])
        town.auto_stream_quests = False
        self.assertIn("auto_stream_quests is off", await self.logged(town, {}))

        town = self.make_town([player()])
        town.manager.middleman_connected = False
        self.assertIn("middleman isn't connected", await self.logged(town, {}))

        town = self.make_town([player()])
        town.channel_restart_lock.locked.return_value = True
        self.assertIn("restarting", await self.logged(town, {}))

    async def test_a_crash_is_logged_and_the_routine_lives_on(self):
        town = self.make_town([player()])
        town.get_players = mock.AsyncMock(side_effect=ValueError("bad player"))
        text = await self.logged(town, self.offers("1. Catch 300 fish (60 min)"))
        self.assertIn("stream quest check failed", text)
        self.assertIn("bad player", text)


class QuestEnd(unittest.IsolatedAsyncioTestCase):
    """The formats are `MSG_STREAM_QUEST_DONE`, `_OUT_OF_TIME` and `_STOPPED`, which the game sends without their meta."""

    ENDED = (
        "Stream quest done: {quest}! The village got {village}, and {helpers} helpers shared {coins} coins. Top helpers: {top}.",
        "Time ran out for the stream quest: {quest} ({progress}/{amount}). So close! Next time.",
        "The stream quest was stopped: {quest}.",
    )

    def make_town(self) -> RFChannel:
        town = RFChannel.__new__(RFChannel)
        town.rfloc = shared_localization()
        town.named_observes = deque()
        town.auto_end_ravebridge = False
        town.ravenfall_loc_strings_path = None
        town.next_stream_quest_check = ravenfallchannel.time.time() + 45
        town.scroll_uses = deque()
        town.watched_stream_quest = RunningQuest("Win 3 raids", "raid", 3, 0)
        return town

    async def announce(self, town: RFChannel, message_format: str):
        await town.process_ravenfall_message({
            "Identifier": "message",
            "Format": message_format,
            "Args": [],
            "CorrelationId": "",
            "Recipent": {"Platform": "system", "PlatformUserName": ""},
        }, None)

    async def test_each_ending_brings_the_check_to_a_second_away(self):
        for message_format in self.ENDED:
            with self.subTest(message_format):
                town = self.make_town()
                await self.announce(town, message_format)
                left = town.next_stream_quest_check - ravenfallchannel.time.time()
                self.assertAlmostEqual(left, 1, delta=0.5)
                self.assertIsNone(town.watched_stream_quest)

    async def test_a_quest_starting_or_running_does_not(self):
        town = self.make_town()
        check = town.next_stream_quest_check
        await self.announce(town, RUNNING_FORMAT)
        await self.announce(town, OFFERS_FORMAT)
        await self.announce(town, "Stream quest! {quest} within {minutes} minutes, all of us together. Reward: {reward}. Just play to help!")
        self.assertEqual(town.next_stream_quest_check, check)

    async def test_every_scroll_use_is_recorded(self):
        town = self.make_town()
        for message_format in (
            "You have used a Raid Scroll.",
            "You have used a Raid Scroll and joined the raid. Good luck!",
            "You have used a Dungeon Scroll.",
            "You have used a Dungeon Scroll and joined the dungeon. Good luck!",
        ):
            await self.announce(town, message_format)
        self.assertEqual(len(town.scroll_uses), 4)


class QuestToggle(unittest.IsolatedAsyncioTestCase):
    async def test_toggles_the_town_and_says_which_way(self):
        cog = GameCog.__new__(GameCog)
        ctx = SimpleNamespace(message=SimpleNamespace(reply=mock.AsyncMock()))
        town = SimpleNamespace(auto_stream_quests=True, channel_name="town")

        await cog.rfquesttoggle(ctx, channel=town)
        self.assertIs(town.auto_stream_quests, False)
        ctx.message.reply.assert_awaited_once_with("Auto stream quests are now disabled for town.")

        await cog.rfquesttoggle(ctx, channel=town)
        self.assertIs(town.auto_stream_quests, True)


class QuestList(RoutineTestCase):
    async def read(self, town: RFChannel, reply: dict | None):
        response = {"success": True, "responses": [reply] if reply else []}
        with mock.patch.object(ravenfallchannel, "send_to_server_and_wait_response", mock.AsyncMock(return_value=response)):
            return await town.read_stream_quests()

    async def test_lists_the_offers_on_one_line_and_saves_them(self):
        town = self.make_town([])
        quests = await self.read(town, offers_reply("1. Chop 1,200 wood (60 min), 2. Win 3 raids (120 min)"))
        self.assertEqual(
            format_quest_list(*quests),
            "Today's stream quests: 1. Chop 1,200 wood (60 min) ✦ 2. Win 3 raids (120 min)",
        )
        self.assertEqual(len(town.get_saved_stream_quest_offers()), 2)

    async def test_running_quest_uses_the_offers_saved_today(self):
        town = self.make_town([])
        await self.read(town, offers_reply("1. Chop 1,200 wood (60 min), 2. Win 3 raids (120 min)"))
        quests = await self.read(town, running_reply("Win 3 raids", "1", "3"))
        text = format_quest_list(*quests)
        self.assertNotIn("\n", text)
        self.assertEqual(
            text,
            "Today's stream quests: 1. Chop 1,200 wood (60 min) ✦ 2. Win 3 raids (120 min)"
            " ✦ Running now: Win 3 raids (1/3), 100 min left",
        )

    async def test_running_quest_with_nothing_saved_says_so(self):
        town = self.make_town([])
        text = format_quest_list(*await self.read(town, running_reply()))
        self.assertTrue(text.startswith("The game doesn't list"))
        self.assertTrue(text.endswith(" ✦ Running now: Win 3 raids (0/3), 100 min left"))

    async def test_no_offers_left(self):
        town = self.make_town([])
        town.saved_stream_quest_offers = parse_offers(offers_reply("1. Chop 1,200 wood (60 min)"))
        town.saved_stream_quest_offers_day = ravenfallchannel.datetime.now(ravenfallchannel.timezone.utc).date()
        self.assertEqual(format_quest_list(*await self.read(town, offers_reply(""))), "No stream quests left today")

    async def test_no_answer(self):
        self.assertIsNone(await self.read(self.make_town([]), None))

    async def test_command_replies(self):
        cog = InfoCog.__new__(InfoCog)
        ctx = SimpleNamespace(message=SimpleNamespace(reply=mock.AsyncMock()))
        town = SimpleNamespace(read_stream_quests=mock.AsyncMock(return_value=None))
        await cog.streamquests(ctx, channel=town)
        ctx.message.reply.assert_awaited_once_with("The game didn't answer. Try again in a bit.")


class Template(unittest.TestCase):
    def test_correlation_id_is_kept_for_the_reply(self):
        message = json.loads(RavenBotTemplates.stream_quest_status({"Username": "town"}, correlation_id="abc"))
        self.assertEqual(message["CorrelationId"], "abc")


if __name__ == "__main__":
    unittest.main()
