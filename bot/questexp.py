"""The exp in a quest reward, as a share of the player's current level.

The game writes quest rewards as "66,550 coins and 30,788,099 Gathering exp"
(`UI_QUEST_REWARD`), in the messages listed in `QUEST_EXP_KEYS`.
"""

from __future__ import annotations

import re
from typing import Iterable, NamedTuple

from ravenpy import experience_for_level

from .models import Player

QUEST_EXP_KEYS = frozenset({
    "quest_taken",
    "quest_done",
    "quest_weekly_done",
    "quest_event_done",
    "quest_event_today",
    "quest_chain_finished",
})
EXP_PERCENT_ARG = "expPercent"

REWARD_EXP: re.Pattern = re.compile(r"([\d,]+) ([A-Za-z]+) exp\b")


class RewardExp(NamedTuple):
    amount: int
    skill: str
    """The skill's key in `Player['stats']`, such as `gathering`."""


def find_reward_exp(args: Iterable) -> RewardExp | None:
    """The exp reward in the first message argument that has one."""
    for arg in args:
        if not isinstance(arg, str):
            continue
        mo = REWARD_EXP.search(arg)
        if mo:
            return RewardExp(int(mo.group(1).replace(",", "")), mo.group(2).lower())
    return None


def exp_percent(reward: RewardExp, player: Player | None) -> str | None:
    """The reward's exp as a percentage of the exp the player's current level takes, like `4.21%`."""
    if not isinstance(player, dict) or not player:
        return None
    stat = (player.get("stats") or {}).get(reward.skill)
    if not isinstance(stat, dict):
        return None
    level_exp = experience_for_level(stat["level"] + 1)
    if level_exp <= 0:
        return None
    return f"{reward.amount / level_exp:.2%}"
