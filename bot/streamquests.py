"""Picks the stream quest a town is most likely to finish.

`!quest stream` names today's three offers. An offer is one goal the whole town
works on against a time limit, and RavenNest scales its target to the players in the
town. This module reads the offers from the game's reply and estimates how much of
each target the town would reach in time. The game source says how each kind of
quest is counted: see `StreamQuestManager` and `QuestTracker` in RavenNest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from ravenpy import experience_for_level

GATHER = "gather"
KILL = "kill"
LEVELS = "levels"
RAID = "raid"
DUNGEON = "dungeon"
MAKE = "make"
HERO_WAVE = "hero_wave"

# `ItemDropRateSettings.ResourceGatherInterval` is 10 seconds per player
GATHER_ROUNDS_PER_MINUTE = 6

# Rounded down from one quest: 82 helpers defeated 10,560 enemies in 11 minutes, about
# 11.6 each a minute. The bot can't read kill counts, and that town was high level
KILLS_PER_MINUTE = 10

# The middle of the timers in the game's MainWorld scene (1500-8000 and 1500-9000 s)
SECONDS_BETWEEN_RAIDS = 4750
SECONDS_BETWEEN_DUNGEONS = 5250

# What `training` says for each fighter. "Melee" is train all
FIGHTING = frozenset({"attack", "defense", "strength", "health", "melee", "all", "magic", "ranged"})

_OFFER = re.compile(r"(\d+)\.\s+(.+?)\s+\((\d+)\s+min\)")

_NUMBER = r"([\d,]+)"
_OFFER_TEXTS: tuple[tuple[re.Pattern[str], str, Optional[str]], ...] = (
    (re.compile(rf"^Chop {_NUMBER} wood$"), GATHER, "woodcutting"),
    (re.compile(rf"^Mine {_NUMBER} ore$"), GATHER, "mining"),
    (re.compile(rf"^Catch {_NUMBER} fish$"), GATHER, "fishing"),
    (re.compile(rf"^Harvest {_NUMBER} wheat$"), GATHER, "farming"),
    (re.compile(rf"^Gather {_NUMBER} times$"), GATHER, "gathering"),
    (re.compile(rf"^Defeat {_NUMBER} enemies$"), KILL, None),
    (re.compile(rf"^Gain {_NUMBER} levels$"), LEVELS, None),
    (re.compile(rf"^Win {_NUMBER} raids$"), RAID, None),
    (re.compile(r"^Win a raid$"), RAID, None),
    (re.compile(rf"^Clear {_NUMBER} dungeons$"), DUNGEON, None),
    (re.compile(r"^Clear a dungeon$"), DUNGEON, None),
    (re.compile(rf"^Craft, cook or brew {_NUMBER} items$"), MAKE, None),
    (re.compile(rf"^Hold {_NUMBER} waves on Ravebridge in hero mode$"), HERO_WAVE, None),
)


@dataclass(frozen=True)
class StreamQuestOffer:
    slot: int
    """The number `!quest stream start` takes, from 1."""
    text: str
    objective: Optional[str]
    """None for wording this module doesn't know, which is never picked."""
    amount: int
    minutes: int
    skill: Optional[str] = None


@dataclass(frozen=True)
class TownActivity:
    """What the town is doing now, which is taken to carry on for the whole quest."""

    players: list[Mapping[str, Any]]
    levels_per_minute: float = 0
    queued_raids: int = 0
    queued_dungeons: int = 0


def parse_offer(slot: int, text: str, minutes: int) -> StreamQuestOffer:
    for pattern, objective, skill in _OFFER_TEXTS:
        match = pattern.match(text)
        if match:
            amount = int(match.group(1).replace(",", "")) if match.groups() else 1
            return StreamQuestOffer(slot, text, objective, amount, minutes, skill)
    return StreamQuestOffer(slot, text, None, 0, minutes)


def parse_offers(message: Mapping[str, Any]) -> list[StreamQuestOffer]:
    """The offers in a reply to `!quest stream`.

    The list is empty if a quest or the streamer's goal is running, because only
    the offers reply has `{offers}` in its format, or if no offers are left.
    """
    args = message.get("Args") or []
    if "{offers}" not in (message.get("Format") or "") or not args:
        return []
    return [
        parse_offer(int(match.group(1)), match.group(2), int(match.group(3)))
        for match in _OFFER.finditer(str(args[0]))
    ]


def is_working(player: Mapping[str, Any]) -> bool:
    """Whether the player is at their training, which is where quest progress comes from."""
    if player.get("training") in (None, "", "None") or player.get("island") in (None, "", "None"):
        return False
    return not any(
        player.get(state)
        for state in ("sailing", "resting", "inarena", "induel", "indungeon", "inraid")
    )


def _workers(players: Iterable[Mapping[str, Any]], trained: Iterable[str]) -> int:
    trained = set(trained)
    return sum(
        1 for player in players
        if is_working(player) and str(player["training"]).lower() in trained
    )


def expected_progress(offer: StreamQuestOffer, town: TownActivity) -> float:
    """How far the town should get on this offer within its time limit."""
    if not town.players:
        return 0
    if offer.objective == GATHER:
        return _workers(town.players, [offer.skill]) * GATHER_ROUNDS_PER_MINUTE * offer.minutes
    if offer.objective == KILL:
        return _workers(town.players, FIGHTING) * KILLS_PER_MINUTE * offer.minutes
    if offer.objective == LEVELS:
        return town.levels_per_minute * offer.minutes
    if offer.objective == RAID:
        return town.queued_raids + offer.minutes * 60 / SECONDS_BETWEEN_RAIDS
    if offer.objective == DUNGEON:
        return town.queued_dungeons + offer.minutes * 60 / SECONDS_BETWEEN_DUNGEONS
    # Items only count from !craft, !cook and !brew, never from training, and
    # hero waves need the streamer playing their hero
    return 0


def completion_ratio(offer: StreamQuestOffer, town: TownActivity) -> float:
    """The expected progress as a share of the target. Above 1 the town should finish."""
    if offer.amount <= 0:
        return 0
    return expected_progress(offer, town) / offer.amount


def pick_offer(offers: Iterable[StreamQuestOffer], town: TownActivity) -> Optional[StreamQuestOffer]:
    """The offer the town is most likely to finish, or None if it should finish none in time."""
    best = max(offers, key=lambda offer: completion_ratio(offer, town), default=None)
    if best is None or completion_ratio(best, town) < 1:
        return None
    return best


def _skill_levels(player: Mapping[str, Any]) -> dict[str, float]:
    """Each skill's level, with the experience towards the next one as a fraction."""
    levels = {}
    for name, stat in (player.get("stats") or {}).items():
        if not isinstance(stat, Mapping):
            continue
        needed = experience_for_level(stat["level"] + 1)
        levels[name] = stat["level"] + (stat["experience"] / needed if needed else 0)
    return levels


def levels_per_minute(
    before: Iterable[Mapping[str, Any]],
    after: Iterable[Mapping[str, Any]],
    seconds: float,
) -> float:
    """The levels the town gains per minute, from two player lists `seconds` apart.

    All skills count, and only players in both lists.
    """
    earlier = {player["id"]: _skill_levels(player) for player in before}
    gained = 0.0
    for player in after:
        if player["id"] not in earlier:
            continue
        old = earlier[player["id"]]
        for name, level in _skill_levels(player).items():
            gained += max(0.0, level - old.get(name, level))
    return gained * 60 / seconds
