"""Run with: python -m unittest tests.test_ravenfall_loc"""

import unittest

from bot.ravenfallloc import Match, RavenfallLocalization, round_skill_bonuses


class RoundSkillBonusesTest(unittest.TestCase):
    def test_rounds_float_noise(self):
        self.assertEqual(
            round_skill_bonuses("Healing 1 [+0.42000002] (0%)"),
            "Healing 1 [+0.42] (0%)",
        )

    def test_rounds_every_bonus_in_a_stats_list(self):
        self.assertEqual(
            round_skill_bonuses("Attack 1 (0%), Woodcutting 527 [+36.89] (63%), Ranged 1 [+0.28000003] (0%), "),
            "Attack 1 (0%), Woodcutting 527 [+36.89] (63%), Ranged 1 [+0.28] (0%), ",
        )

    def test_drops_trailing_zeros(self):
        self.assertEqual(round_skill_bonuses("[+3.100001]"), "[+3.1]")
        self.assertEqual(round_skill_bonuses("[+4.0000001]"), "[+4]")

    def test_leaves_whole_bonuses_and_other_text_alone(self):
        for text in ("Mining 608 [+36] (37%)", "Cost 1.23456 coins", "[+abc]"):
            self.assertEqual(round_skill_bonuses(text), text)


class DefinitionMatchingTest(unittest.TestCase):
    def test_placeholder_braces_are_kept_in_regex_definitions(self):
        match = Match("k", "You have successfully {{term}} {quantity} {itemName}")
        self.assertIsNotNone(
            match.regex.match("You have successfully crafted {quantity} {itemName}")
        )

    def test_logged_strings_are_identified(self):
        loc = RavenfallLocalization("data/definitions.yaml")
        for text, key in [
            ("Victorious!! The dungeon boss was slain and yielded {itemCount} item treasures!", "dungeon_finished_with_treasure"),
            ("You have successfully cooked {quantity} {itemName}", "produce_success"),
            ("Quest taken: {quest}. Reward: {reward}.", "quest_taken"),
            ("Increased Healing Power +40% (00:09:55), Increased Experience Gain +12% (00:02:49)", "stat_boosts"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(loc.identify_string(text).key, key)


if __name__ == "__main__":
    unittest.main()
