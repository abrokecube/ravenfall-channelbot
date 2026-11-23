import unittest
from collections import deque
from unittest import mock

from ravenpy import experience_for_level

from bot import ravenfallchannel
from bot.questexp import RewardExp, exp_percent, find_reward_exp
from bot.ravenfallchannel import RFChannel
from bot.ravenfallloc import TranslatedString
from shared_loc import shared_localization

QUEST_TAKEN = "Quest taken: {quest}. Reward: {reward}."
REWARD = "66,550 coins and 30,788,099 Gathering exp"


def player(level: int, skill: str = "gathering") -> dict:
    return {"name": "a", "stats": {skill: {"level": level, "currentvalue": level, "maxlevel": level, "experience": 0}}}


class FindRewardExp(unittest.TestCase):
    def test_reads_the_amount_and_skill(self):
        self.assertEqual(find_reward_exp(["Gather 1,680 times", REWARD]), RewardExp(30788099, "gathering"))

    def test_none_without_exp(self):
        self.assertIsNone(find_reward_exp(["Gather 1,680 times", "66,550 coins", 3]))

    def test_skips_the_exp_in_an_exp_quests_task(self):
        args = ["Gain 28,402,852 Strength exp", "7,970 coins and 1,000 Strength exp"]
        self.assertEqual(find_reward_exp(args), RewardExp(1000, "strength"))


class ExpPercent(unittest.TestCase):
    def test_share_of_the_current_level(self):
        expected = f"{30788099 / experience_for_level(101):.2%}"
        self.assertEqual(exp_percent(RewardExp(30788099, "gathering"), player(100)), expected)

    def test_none_without_the_player_or_skill(self):
        reward = RewardExp(100, "gathering")
        self.assertIsNone(exp_percent(reward, None))
        self.assertIsNone(exp_percent(reward, {}))
        self.assertIsNone(exp_percent(reward, player(10, "mining")))


class Translation(unittest.IsolatedAsyncioTestCase):
    def make_town(self, translation: str, found) -> RFChannel:
        town = RFChannel.__new__(RFChannel)
        town.rfloc = shared_localization()
        town.named_observes = deque()
        town.auto_end_ravebridge = False
        town.ravenfall_loc_strings_path = "strings.yaml"
        town.scroll_uses = deque()
        town.record_character = mock.AsyncMock()
        town.process_auto_raid_sessionless = mock.AsyncMock()
        town.get_player = mock.AsyncMock(return_value=found)
        patch = mock.patch.dict(town.rfloc.translated_strings, {"quest_taken": TranslatedString("quest_taken", [translation])})
        patch.start()
        self.addCleanup(patch.stop)
        return town

    async def translate(self, town: RFChannel) -> str:
        message = {
            "Identifier": "message",
            "Format": QUEST_TAKEN,
            "Args": ["Gather 1,680 times", REWARD],
            "CorrelationId": "",
            "Recipent": {"Platform": "twitch", "PlatformUserName": "a", "PlatformId": "1", "CharacterId": "c"},
        }
        out = await town.process_ravenfall_message(message, None)
        return out["Format"]

    async def test_fills_the_percentage(self):
        town = self.make_town("Reward: {reward} ({expPercent} of a level)", player(100))
        expected = f"{30788099 / experience_for_level(101):.2%}"
        self.assertEqual(await self.translate(town), f"Reward: {REWARD} ({expected} of a level)")
        town.get_player.assert_awaited_once_with("a")

    async def test_unknown_when_the_player_cannot_be_read(self):
        town = self.make_town("Reward: {reward} ({expPercent})", None)
        self.assertEqual(await self.translate(town), f"Reward: {REWARD} (?%)")

    async def test_no_query_when_the_translation_does_not_use_it(self):
        town = self.make_town("Reward: {reward}", player(100))
        self.assertEqual(await self.translate(town), f"Reward: {REWARD}")
        town.get_player.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
