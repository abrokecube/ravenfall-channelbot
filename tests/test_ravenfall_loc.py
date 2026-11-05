"""Run with: python -m unittest tests.test_ravenfall_loc"""

import unittest

from bot.ravenfallloc import round_skill_bonuses


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


if __name__ == "__main__":
    unittest.main()
