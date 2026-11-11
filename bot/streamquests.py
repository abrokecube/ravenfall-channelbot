"""Picks the stream quest a town is most likely to finish.

`!quest stream` names today's three offers. An offer is one goal the whole town
works on against a time limit, and RavenNest scales its target to the players in the
town. This module reads the offers from the game's reply and estimates how much of
each target the town would reach in time. The game source says how each kind of
quest is counted: see `StreamQuestManager` and `QuestTracker` in RavenNest.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

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

# Scroll uses are read over this long, and a raid or dungeon quest this long without
# an answer is compared with the other offers
SCROLL_USE_WINDOW_SECONDS = 15 * 60

# What `training` says for each fighter. "Melee" is train all
FIGHTING = frozenset({"attack", "defense", "strength", "health", "melee", "all", "magic", "ranged"})

_OFFER = re.compile(r"(\d+)\.\s+(.+?)\s+\((\d+)\s+min\)")

# The keys in `data/definitions.yaml` of the game saying a stream quest is over
QUEST_END_KEYS = frozenset({"stream_quest_done", "stream_quest_out_of_time", "stream_quest_stopped"})
QUEST_STATUS_KEY = "stream_quest_info"
SCROLL_USE_KEYS = frozenset({
    "raid_scroll_used",
    "raid_scroll_used_joined",
    "dungeon_scroll_used",
    "dungeon_scroll_used_joined",
})

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
class RunningQuest:
    text: str
    objective: Optional[str]
    amount: int
    progress: int
    minutes_left: Optional[int] = None


@dataclass(frozen=True)
class TownActivity:
    """What the town is doing now, which is taken to carry on for the whole quest."""

    players: list[Mapping[str, Any]]
    levels_per_minute: float = 0
    raid_scrolls: int = 0
    """Raid scrolls the town has in stock, whether or not any are queued."""
    dungeon_scrolls: int = 0
    raids_queued: int = 0
    dungeons_queued: int = 0
    scroll_uses: int = 0
    """Raid and dungeon scrolls used together in the last `SCROLL_USE_WINDOW_SECONDS`.
    Either kind says someone is in chat to use more."""


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


def parse_running(message: Mapping[str, Any]) -> RunningQuest:
    """The quest in a `stream_quest_info` reply. Its args are the quest, progress and amount, then the rest."""
    args = message["Args"]
    text, progress, amount = args[:3]
    offer = parse_offer(0, text, 0)
    minutes_left = _number(args[3]) if len(args) > 3 else None
    return RunningQuest(text, offer.objective, _number(amount), _number(progress), minutes_left)


def _number(text: Any) -> int:
    return int(str(text).replace(",", ""))


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


def _queued_and_stock(offer: StreamQuestOffer, town: TownActivity) -> tuple[int, int]:
    if offer.objective == RAID:
        return town.raids_queued, town.raid_scrolls
    return town.dungeons_queued, town.dungeon_scrolls


def _scrolls_expected(offer: StreamQuestOffer, town: TownActivity) -> float:
    """Queued scrolls will be used. The rest of the stock goes at the recent pace."""
    queued, stock = _queued_and_stock(offer, town)
    queued = min(queued, stock)
    paced = town.scroll_uses * offer.minutes * 60 / SCROLL_USE_WINDOW_SECONDS
    return queued + min(stock - queued, paced)


def waits_on_natural_events(offer: StreamQuestOffer, town: TownActivity) -> bool:
    """Whether only the game's own timers would move this offer: nobody is using scrolls."""
    if offer.objective not in (RAID, DUNGEON):
        return False
    return town.scroll_uses == 0 and _queued_and_stock(offer, town)[0] == 0


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
        return _scrolls_expected(offer, town) + offer.minutes * 60 / SECONDS_BETWEEN_RAIDS
    if offer.objective == DUNGEON:
        return _scrolls_expected(offer, town) + offer.minutes * 60 / SECONDS_BETWEEN_DUNGEONS
    # Items only count from !craft, !cook and !brew, never from training, and
    # hero waves need the streamer playing their hero
    return 0


def completion_ratio(offer: StreamQuestOffer, town: TownActivity) -> float:
    """The expected progress as a share of the target. Above 1 the town should finish."""
    if offer.amount <= 0:
        return 0
    return expected_progress(offer, town) / offer.amount


def pick_offer(offers: Iterable[StreamQuestOffer], town: TownActivity) -> Optional[StreamQuestOffer]:
    """The offer the town is most likely to finish, or None if it should finish none in time.

    An offer waiting on natural raids or dungeons is a last resort.
    """
    finishable = [offer for offer in offers if completion_ratio(offer, town) >= 1]
    return max(
        finishable,
        key=lambda offer: (not waits_on_natural_events(offer, town), completion_ratio(offer, town)),
        default=None,
    )


def natural_wait_minutes(quest: RunningQuest) -> float:
    """How long the rest of a raid or dungeon quest takes on the game's own timers."""
    seconds = SECONDS_BETWEEN_RAIDS if quest.objective == RAID else SECONDS_BETWEEN_DUNGEONS
    return (quest.amount - quest.progress) * seconds / 60


def faster_offer(
    offers: Iterable[StreamQuestOffer], quest: RunningQuest, town: TownActivity
) -> Optional[StreamQuestOffer]:
    """The offer that should finish soonest, if that beats waiting out the running quest."""
    candidates = [
        offer for offer in offers
        if offer.objective != quest.objective
        and completion_ratio(offer, town) >= 1
        and not waits_on_natural_events(offer, town)
    ]

    def finish_minutes(offer: StreamQuestOffer) -> float:
        # Progress is taken to be even over the offer's time limit
        return offer.minutes / completion_ratio(offer, town)

    best = min(candidates, key=finish_minutes, default=None)
    if best is None or finish_minutes(best) >= natural_wait_minutes(quest):
        return None
    return best


def describe_offers(offers: Iterable[StreamQuestOffer], town: TownActivity) -> str:
    """Each offer with its expected share of the target, for the log."""
    return ", ".join(
        f"{offer.slot}. {offer.text} ({completion_ratio(offer, town):.0%})"
        for offer in offers
    )


QUEST_LIST_SEPARATOR = " ✦ "


def describe_running(quest: RunningQuest) -> str:
    text = f"Running now: {quest.text} ({quest.progress:,}/{quest.amount:,})"
    if quest.minutes_left is not None:
        text += f", {quest.minutes_left} min left"
    return text


def format_quest_list(offers: Iterable[StreamQuestOffer], running: Optional[RunningQuest] = None) -> str:
    """Today's offers for chat, on one line, with the running quest after them."""
    parts = [f"{offer.slot}. {offer.text} ({offer.minutes} min)" for offer in offers]
    if parts:
        parts[0] = f"Today's stream quests: {parts[0]}"
    elif running is not None:
        parts.append("The game doesn't list today's stream quests while one is running, and I haven't seen them today")
    else:
        parts.append("No stream quests left today")
    if running is not None:
        parts.append(describe_running(running))
    return QUEST_LIST_SEPARATOR.join(parts)
