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
from bot.ravenfallchannel import RFChannel
from bot.streamquests import (
    TownActivity,
    completion_ratio,
    is_working,
    parse_offer,
    parse_offers,
    pick_offer,
)

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
        with_scrolls = completion_ratio(offer, TownActivity(players=[player()], raid_scrolls=3))
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
        town.next_stream_quest_attempt = 0
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
        self.prometheus.assert_not_awaited()

        town = self.make_town([player("Fishing")])
        await self.tick(town, self.offers("1. Catch 300 fish (60 min), 2. Gain 10 levels (120 min)"))
        self.prometheus.assert_awaited_once()
        town.send_chat_message.assert_awaited_once_with("!quest stream start 1")

    async def test_scrolls_in_stock_reach_the_estimate(self):
        town = self.make_town([player()])
        await self.tick(
            town,
            self.offers("1. Clear 2 dungeons (120 min), 2. Win 3 raids (120 min)"),
            self.scroll_counts(raid=5),
        )
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
        reply = {"success": True, "responses": [{"Format": RUNNING_FORMAT, "Args": ["Chop 300 wood"]}]}
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

        town = self.make_town([player()])
        town.next_stream_quest_attempt = ravenfallchannel.time.time() + 600
        self.assertIn("next try in", await self.logged(town, {}))

    async def test_a_crash_is_logged_and_the_routine_lives_on(self):
        town = self.make_town([player()])
        town.get_players = mock.AsyncMock(side_effect=ValueError("bad player"))
        text = await self.logged(town, self.offers("1. Catch 300 fish (60 min)"))
        self.assertIn("stream quest check failed", text)
        self.assertIn("bad player", text)


class Template(unittest.TestCase):
    def test_correlation_id_is_kept_for_the_reply(self):
        message = json.loads(RavenBotTemplates.stream_quest_status({"Username": "town"}, correlation_id="abc"))
        self.assertEqual(message["CorrelationId"], "abc")


if __name__ == "__main__":
    unittest.main()
