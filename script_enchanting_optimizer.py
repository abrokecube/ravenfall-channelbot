# pyright: basic
# ruff: noqa: CPY001, T201
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

import ravenpy
from ravenpy import Item
from utils.format_time import TimeSize, format_seconds

MAX_LEVEL = 999
ENCHANTMENT_INTERVAL = 60  # minutes between successful enchants
MIN_ENCHANTMENT_TIME = 30  # seconds
LOW_LUCK_LEVEL = 10
LOW_LUCK_MIN_CHANCE = 0.05
LOW_LUCK_CHANCE = 0.5
MID_LUCK_LEVEL = 20
MID_LUCK_MIN_CHANCE = 0.1
MID_LUCK_CHANCE = 0.25

ENCHANTABLE_CATEGORIES = (
    ravenpy.ItemCategory.Weapon,
    ravenpy.ItemCategory.Armor,
    ravenpy.ItemCategory.Ring,
    ravenpy.ItemCategory.Amulet,
    ravenpy.ItemCategory.Cosmetic,
)


# ---------------------------------------------------------------------------
# Exact port of GameMath.cs (RavenNest.BusinessLogic.Utilities)
# ---------------------------------------------------------------------------


def get_item_level(item: Item) -> int:
    """Get the item's effective enchanting level.

    Mirrors ``GameMath.GetItemLevel``: the sum of equip requirements,
    unless the item's own level is higher.
    """
    req_sum = sum(req.level for req in item.equip_requirements)
    return max(req_sum, item.level)


def get_max_enchanting_attribute_count(item_level: int) -> int:
    """Get the max number of attributes an item that level can gain."""
    return max(1, math.floor(math.floor(item_level / 10) / 5))


def get_max_enchantment_count_by_skill(skill_level: int) -> int:
    """Get the max enchantment attributes the skill level can apply."""
    # NOTE: mirrors the game code where Math.Max makes the cap >= 10
    return math.floor(max(10, math.floor(skill_level / 3)))


def get_enchanting_experience(
    skill_level: int, attribute_count: int, item_level: int
) -> int:
    """Get the exp gained for successfully enchanting an item.

    Mirrors the game's exp formula, capped to 0.1%-150% of the exp
    required for the next clan skill level.
    """
    exp = item_level * 100.0
    exp += attribute_count * 25.0
    exp += exp * (skill_level / MAX_LEVEL) * 0.25
    exp_for_level = ravenpy.experience_for_level(skill_level + 1)
    return math.trunc(max(exp_for_level * 0.001, min(exp, exp_for_level * 1.5)))


def get_cooldown_seconds(clan_skill_level: int, scale: float) -> float:
    """Get the post-enchant cooldown in seconds (min 30s)."""
    cooldown = ENCHANTMENT_INTERVAL * 60 * scale - clan_skill_level
    return max(MIN_ENCHANTMENT_TIME, cooldown)


# ---------------------------------------------------------------------------
# One enchant attempt, statistically (port of EnchantmentManager.EnchantItem)
# ---------------------------------------------------------------------------


class AttemptStats:
    """Expected outcome of one enchant attempt, incl. failures and luck."""

    def __init__(self, item_level: int, skill_level: int):
        self.item_level = item_level
        self.skill_level = skill_level
        self.slots = get_max_enchanting_attribute_count(item_level)

        p = skill_level / item_level
        self.success_chance = p

        c1 = min(p, 1.0)
        c2 = min(p * 1.33, 1.0)
        c3 = min(p * 2, 1.0)

        max_ench = get_max_enchantment_count_by_skill(skill_level)

        self.xp_full = get_enchanting_experience(
            skill_level, min(max_ench, max(1, self.slots)), item_level
        )
        self.xp_half = get_enchanting_experience(
            skill_level,
            min(max_ench, max(1, math.floor(self.slots * 0.5))),
            item_level,
        )
        self.xp_third = get_enchanting_experience(
            skill_level,
            min(max_ench, max(1, math.floor(self.slots * 0.33))),
            item_level,
        )
        self.xp_one = get_enchanting_experience(skill_level, 1, item_level)

        # rng <= p -> full, <= p*1.33 -> half, <= p*2 -> third,
        # rng >= 0.75 -> consolation single attr, else outright fail
        self.p_full = c1
        self.p_half = max(0.0, c2 - c1)
        self.p_third = max(0.0, c3 - c2)
        self.p_cons = max(0.0, 1.0 - max(c3, 0.75))
        p_fail_region = max(0.0, 0.75 - c3)

        # low level luck rescues part of the fail region
        self.p_rescue = 0.0
        if p_fail_region > 0:
            if skill_level < LOW_LUCK_LEVEL and p >= LOW_LUCK_MIN_CHANCE:
                self.p_rescue = max(0.0, LOW_LUCK_CHANCE - c3)
            elif skill_level < MID_LUCK_LEVEL and p >= MID_LUCK_MIN_CHANCE:
                self.p_rescue = max(0.0, MID_LUCK_CHANCE - c3)

        self.p_success = (
            self.p_full + self.p_half + self.p_third + self.p_cons + self.p_rescue
        )

        cooldown_factor = min(1.0, 1.0 / p)
        success_cd = get_cooldown_seconds(skill_level, cooldown_factor)
        fail_cd = get_cooldown_seconds(skill_level, cooldown_factor * 0.1)
        self.exp_time_per_attempt = (
            self.p_success * success_cd + (1.0 - self.p_success) * fail_cd
        )

        expected_xp = (
            self.p_full * self.xp_full
            + self.p_half * self.xp_half
            + self.p_third * self.xp_third
            + (self.p_cons + self.p_rescue) * self.xp_one
        )
        self.exp_xp = expected_xp
        self.exp_xp_per_attempt = expected_xp
        self.xp_per_hour = expected_xp / self.exp_time_per_attempt * 3600

    @property
    def xp_per_success(self) -> float:
        """Average exp gained per successful enchant."""
        return self.exp_xp_per_attempt / self.p_success


# ---------------------------------------------------------------------------
# Multi-account throughput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fleet:
    """The set of accounts enchanting for the same clan.

    Every account has its own enchant cooldown, but all of them feed the
    same clan skill. ``max_rate`` caps the combined attempts per minute
    (command/API rate limit); 0 means unlimited.
    """

    accounts: int = 1
    max_rate: float = 0.0

    def attempts_per_second(self, stats: AttemptStats) -> float:
        """Combined attempts/s: cooldown-bound, then capped by the rate limit."""
        rate = self.accounts / stats.exp_time_per_attempt
        if self.max_rate > 0:
            rate = min(rate, self.max_rate / 60)
        return rate

    def xp_per_hour(self, stats: AttemptStats) -> float:
        """Combined expected exp/hour of all accounts."""
        return self.attempts_per_second(stats) * stats.exp_xp_per_attempt * 3600

    def is_rate_limited(self, stats: AttemptStats) -> bool:
        """Whether the rate limit, not the cooldown, is the bottleneck."""
        return 0 < self.max_rate / 60 < self.accounts / stats.exp_time_per_attempt


# ---------------------------------------------------------------------------
# Item ranking
# ---------------------------------------------------------------------------


def get_enchantable_items(max_item_level: int = 0) -> list[tuple[Item, int]]:
    """Get all enchantable items with their effective item level."""
    out: list[tuple[Item, int]] = []
    for item in ravenpy.get_all_items():
        if item.category not in ENCHANTABLE_CATEGORIES:
            continue
        level = get_item_level(item)
        if level < 1:
            continue
        if max_item_level and level > max_item_level:
            continue
        out.append((item, level))
    return out


def rank_items(
    skill_level: int,
    fleet: Fleet,
    max_item_level: int = 0,
) -> list[tuple[Item, int, AttemptStats]]:
    """Rank enchantable items by combined expected exp/hour, best first."""
    rows: list[tuple[Item, int, AttemptStats]] = []
    for item, level in get_enchantable_items(max_item_level):
        stats = AttemptStats(level, skill_level)
        rows.append((item, level, stats))
    rows.sort(key=lambda r: (-fleet.xp_per_hour(r[2]), r[2].item_level, r[0].name))
    return rows


def collapse_by_tie(
    rows: list[tuple[Item, int, AttemptStats]],
    fleet: Fleet,
) -> list[tuple[int, list[Item], AttemptStats]]:
    """Collapse equal-performing rows (same item level) into groups."""
    groups: dict[int, list[Item]] = {}
    stats_by_level: dict[int, AttemptStats] = {}
    for item, level, stats in rows:
        if level not in groups:
            groups[level] = []
            stats_by_level[level] = stats
        groups[level].append(item)
    out: list[tuple[int, list[Item], AttemptStats]] = [
        (level, groups[level], stats_by_level[level]) for level in groups
    ]
    out.sort(key=lambda r: (-fleet.xp_per_hour(r[2]), r[0]))
    return out


def names_for(items: list[Item], max_names: int) -> str:
    """Get representative item names, cheapest vendor price first."""
    items = sorted(items, key=lambda i: (i.buy_price or 10**12, i.name))
    names = [i.name for i in items[:max_names]]
    if len(items) > max_names:
        names.append(f"... +{len(items) - max_names} more")
    return ", ".join(names)


def print_ranking(
    skill_level: int,
    fleet: Fleet,
    max_item_level: int,
    top: int,
    names_per_row: int,
) -> None:
    """Print the ranked best-items table for the current skill level."""
    rows = collapse_by_tie(rank_items(skill_level, fleet, max_item_level), fleet)

    print(f"\nClan 'Enchanting' skill level: {skill_level}")
    limit = f"{fleet.max_rate:g}/min" if fleet.max_rate > 0 else "none"
    print(f"Accounts: {fleet.accounts}   Combined rate limit: {limit}")
    print(
        "Best items to enchant right now "
        "(expected values incl. failures and consolation enchants):\n"
    )
    header = (
        f"{'item lv':>7} {'slots':>5} {'succ%':>6} {'exp/ench':>12} "
        f"{'avg attempt':>13} {'att/min':>8} {'exp/hour':>12}  items"
    )
    print(header)
    print("-" * len(header))
    for level, items, stats in rows[:top]:
        print(
            f"{level:>7} {stats.slots:>5} {min(stats.success_chance, 1.0) * 100:>5.1f}% "
            f"{stats.xp_per_success:>12,.0f} "
            f"{format_seconds(stats.exp_time_per_attempt, TimeSize.SMALL):>13} "
            f"{fleet.attempts_per_second(stats) * 60:>8,.1f} "
            f"{fleet.xp_per_hour(stats):>12,.0f}"
            f"{'*' if fleet.is_rate_limited(stats) else ' '} "
            f"{names_for(items, names_per_row)}"
        )


# ---------------------------------------------------------------------------
# Leveling simulation
# ---------------------------------------------------------------------------

type PlanRow = tuple[int, int, Item, AttemptStats, float, float]


def print_plan_row(row: PlanRow, cumulative: float) -> None:
    """Print one plan row covering a run of levels."""
    start, end, item, stats, attempts, seconds = row
    levels = f"Lv {start}" if end == start else f"Lv {start}-{end}"
    print(
        f"{levels:>10}: {item.name:<28} (item lv {stats.item_level:>3}, "
        f"{stats.slots} slot(s))  {attempts:>6,.0f} ench  "
        f"~{format_seconds(seconds, TimeSize.MEDIUM):>14}  "
        f"total ~{format_seconds(cumulative, TimeSize.MEDIUM):>14}"
    )


def simulate_leveling(
    start_level: int,
    start_exp: float,
    target_level: int,
    fleet: Fleet,
    max_item_level: int,
    show_levels: int = 100,
) -> None:
    """Simulate leveling the clan enchanting skill up to the target level."""
    level = start_level
    exp = start_exp
    while exp >= ravenpy.experience_for_level(level + 1) and level < target_level:
        level += 1

    seconds_elapsed = 0.0
    total_attempts = 0.0
    total_xp = 0.0
    plan_rows: list[PlanRow] = []

    print()
    plan_start = level
    plan_item: Item | None = None
    plan_stats: AttemptStats | None = None
    plan_attempts = 0.0
    plan_seconds = 0.0

    while level < target_level:
        item, _level_req, stats = rank_items(level, fleet, max_item_level)[0]
        next_req = ravenpy.experience_for_level(level + 1)
        exp_needed = next_req - exp
        attempts = math.ceil(exp_needed / stats.exp_xp_per_attempt)
        level_seconds = attempts / fleet.attempts_per_second(stats)

        item_changed = plan_item is None or plan_item.id != item.id
        stats_changed = (
            plan_stats is None
            or plan_stats.exp_xp_per_attempt != stats.exp_xp_per_attempt
        )
        if item_changed or stats_changed:
            if plan_item is not None and plan_stats is not None:
                plan_rows.append(
                    (
                        plan_start,
                        level - 1,
                        plan_item,
                        plan_stats,
                        plan_attempts,
                        plan_seconds,
                    )
                )
            plan_start = level
            plan_attempts = 0.0
            plan_seconds = 0.0
        plan_item = item
        plan_stats = stats

        plan_attempts += attempts
        plan_seconds += level_seconds
        seconds_elapsed += level_seconds
        total_attempts += attempts
        total_xp += attempts * stats.exp_xp_per_attempt

        # carry over the exp overshoot of the last attempt into the next level
        exp = attempts * stats.exp_xp_per_attempt - exp_needed
        level += 1

    if plan_item is not None and plan_stats is not None:
        plan_rows.append(
            (plan_start, level - 1, plan_item, plan_stats, plan_attempts, plan_seconds)
        )

    cumulative = 0.0
    show_until = start_level + show_levels if show_levels > 0 else target_level
    hidden_rows = 0
    for row in plan_rows:
        cumulative += row[5]
        if row[0] >= show_until:
            hidden_rows += 1
            continue
        print_plan_row(row, cumulative)
    if hidden_rows:
        print(
            f"  ... {hidden_rows} more row(s) up to Lv {target_level} hidden "
            f"(see --show-levels); totals below cover the full range"
        )

    total_required = sum_required_exp(start_level, start_exp, target_level)
    avg_xp_per_hour = (
        total_required / (seconds_elapsed / 3600) if seconds_elapsed else 0
    )

    print(
        f"\n{'=' * 60}\n"
        f"Successful enchants needed : ~{total_attempts:,.0f}\n"
        f"Total exp gained           : ~{total_xp:,.0f}"
        f" (of {total_required:,.0f} needed)\n"
        f"{f'Total time ({fleet.accounts} acct)':<27}: "
        f"{format_seconds(seconds_elapsed, TimeSize.LONG)}\n"
        f"Average exp/hour           : {avg_xp_per_hour:,.0f}\n"
        f"{'=' * 60}\n"
    )


def sum_required_exp(start_level: int, start_exp: float, target_level: int) -> int:
    """Sum the exp required to go from start_level/start_exp to the target."""
    required = ravenpy.experience_for_level(start_level + 1) - start_exp
    for level in range(start_level + 1, target_level):
        required += ravenpy.experience_for_level(level + 1)
    return int(required)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Find the best item to enchant for clan Enchanting exp, "
            "and simulate leveling it up. Based on the exact RavenNest "
            "server formulas."
        )
    )
    parser.add_argument(
        "eq_level",
        type=int,
        help="Your clan's current 'Enchanting' skill level (1-999).",
    )
    parser.add_argument(
        "eq_exp",
        type=float,
        nargs="?",
        default=0.0,
        help="Current exp accumulated toward the next enchanting skill level.",
    )
    parser.add_argument(
        "--target-level",
        type=int,
        default=999,
        help="Target enchanting level for the simulation (default: 999).",
    )
    parser.add_argument(
        "--max-item-level",
        type=int,
        default=0,
        help="Only consider items with an item level <= this (0 = no limit).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=12,
        help="Rows to show in the ranking table (default: 12).",
    )
    parser.add_argument(
        "--names",
        type=int,
        default=3,
        help="Item names to show per ranking row (default: 3).",
    )
    parser.add_argument(
        "--accounts",
        type=int,
        default=1,
        help="Number of accounts enchanting for the clan at once (default: 1).",
    )
    parser.add_argument(
        "--max-rate",
        type=float,
        default=0.0,
        help=(
            "Combined enchant attempts per minute all accounts may send "
            "before hitting rate limits (0 = unlimited). When this binds, "
            "exp per attempt matters more than short cooldowns (marked *)."
        ),
    )
    parser.add_argument(
        "--show-levels",
        type=int,
        default=100,
        help=(
            "Only list the plan for this many levels past the current one "
            "(default: 100, 0 = all). Totals still cover the full target."
        ),
    )
    args = parser.parse_args()

    level = min(max(1, args.eq_level), MAX_LEVEL)
    target = min(args.target_level, MAX_LEVEL)
    if target <= level:
        print("Target level must be higher than the current level.")
        return

    fleet = Fleet(max(1, args.accounts), max(0.0, args.max_rate))
    print_ranking(level, fleet, args.max_item_level, args.top, args.names)
    simulate_leveling(
        level, args.eq_exp, target, fleet, args.max_item_level, args.show_levels
    )


if __name__ == "__main__":
    main()
