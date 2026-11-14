"""Plans craft commands to restock the channel shop based on what people buy.

Unlike stockall.py, this does not try to stock every item. An item is crafted only
when its stock is below its target:

    target = max(minimum stock for its category, largest past order × ORDERS_TO_COVER)

Only redeemable items (craftable, not soulbound, priced in item_values.json) get a
target. Ingredients already in stock are used before crafting more of them.

Purchases come from the "Shop purchase: <item> x<count>" rows in the bot's
user_credit_transaction table. That table has no channel column, so every
channel's purchases count. The database is opened read-only.

Run from the repo root:
    python -m scripts.stockdemand --db path/to/data.db
"""
import argparse
import asyncio
import copy
import json
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import os  # noqa: E402

from ravenpy import ravenpy  # noqa: E402

import bot.multichat_command as mc  # noqa: E402
from bot.clients.session import close_session  # noqa: E402
from bot.config import get_settings  # noqa: E402

Cat = ravenpy.ItemCategory

CHANNEL_ID = "756734432"
DB_PATH = "data.db"
HISTORY_DAYS: int | None = None  # None = all purchase history
ORDERS_TO_COVER = 2  # keep enough stock for the largest past order this many times
INGREDIENT_USE_PERCENTAGE = 0.75  # share of raw resources in stock the plan may use

# Every redeemable item in a category is kept at least at this stock.
CATEGORY_MIN_STOCK: dict[ravenpy.ItemCategory, int] = {
    Cat.Weapon: 1,
    Cat.Armor: 1,
    Cat.Ring: 1,
    Cat.Amulet: 1,
    Cat.Food: 10,
    Cat.Potion: 10,
    Cat.Resource: 0,
    Cat.Pet: 0,
    Cat.Scroll: 0,
    Cat.Cosmetic: 0,
    Cat.Skin: 0,
    Cat.StreamerToken: 0,
}
# Per-item minimum stock. Wins over the category value.
MIN_STOCK_OVERRIDES: dict[str, int] = {}
# Items that are never planned as targets (they can still be crafted as ingredients).
EXCLUDE_ITEMS: set[str] = set()

OUT_REPORT = Path("./output/planned_crafts_demand.txt")
OUT_REQUIRED = Path("./output/required_items_demand.json")

PURCHASE_RE = re.compile(r"^Shop purchase: (.+) x(\d+)$")


@dataclass
class Sales:
    orders: int = 0
    units: int = 0
    largest: int = 0


@dataclass
class Target:
    item: ravenpy.Item
    stock: int
    sales: Sales
    minimum: int
    target: int
    need: int
    reason: str
    crafted: int = 0
    not_craftable: bool = False


@dataclass
class Plan:
    available: dict[str, int]
    crafts: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))
    taken: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))


def load_sales(db_path: str, history_days: int | None) -> dict[str, Sales]:
    """Purchases per item name, from the bot's credit transactions."""
    if not Path(db_path).exists():
        raise SystemExit(f"Database not found: {db_path}")
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    query = (
        "SELECT description FROM user_credit_transaction "
        "WHERE description LIKE 'Shop purchase:%'"
    )
    params: tuple = ()
    if history_days is not None:
        since = datetime.now() - timedelta(days=history_days)
        query += " AND timestamp >= ?"
        params = (since.strftime("%Y-%m-%d %H:%M:%S"),)
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(query, params).fetchall()

    lower_names = {name.lower(): name for name in ravenpy._items_name_data}
    sales: dict[str, Sales] = defaultdict(Sales)
    unknown: set[str] = set()
    for (description,) in rows:
        match = PURCHASE_RE.match(description or "")
        if not match:
            continue
        name = lower_names.get(match.group(1).lower())
        if name is None:
            unknown.add(match.group(1))
            continue
        count = int(match.group(2))
        s = sales[name]
        s.orders += 1
        s.units += count
        s.largest = max(s.largest, count)
    if unknown:
        print(f"Purchases of unknown items ignored: {', '.join(sorted(unknown))}")
    print(f"Read {len(rows)} shop purchases from {db_path}")
    return dict(sales)


def load_prices() -> dict[str, int]:
    path = get_settings().item_values_path
    if not os.path.exists(path):
        raise SystemExit(f"Item values not found: {path}")
    with open(path, encoding="utf-8") as f:
        return {name.lower(): price for name, price in json.load(f).items()}


async def load_stock(channel_id: str) -> dict[str, int]:
    """Tradeable item counts across the channel's characters."""
    response = await mc.get_char_items(channel_id)
    if response.get("status") != 200:
        raise SystemExit(f"Could not get channel items: {response.get('error', response)}")
    stock: dict[str, int] = defaultdict(int)
    for char in response["data"]:
        for user_item in char["items"]:
            if user_item["soulbound"] or user_item["equipped"]:
                continue
            item = ravenpy._items_id_data.get(user_item["id"])
            if item is not None:
                stock[item.name] += user_item["amount"]
    return dict(stock)


def build_targets(
    stock: dict[str, int], sales: dict[str, Sales], prices: dict[str, int]
) -> list[Target]:
    targets: list[Target] = []
    for item in ravenpy.get_all_items():
        if item.soulbound or not prices.get(item.name.lower(), 0):
            continue
        if item.name in EXCLUDE_ITEMS:
            continue
        item_sales = sales.get(item.name, Sales())
        minimum = MIN_STOCK_OVERRIDES.get(
            item.name, CATEGORY_MIN_STOCK.get(item.category, 0)
        )
        sales_target = item_sales.largest * ORDERS_TO_COVER
        target = max(minimum, sales_target)
        if target <= 0:
            continue
        have = stock.get(item.name, 0)
        targets.append(
            Target(
                item=item,
                stock=have,
                sales=item_sales,
                minimum=minimum,
                target=target,
                need=max(0, target - have),
                reason="sales" if sales_target >= minimum else "min",
                not_craftable=not item.craft_ingredients,
            )
        )
    return targets


def starting_available(stock: dict[str, int], targets: list[Target]) -> dict[str, int]:
    """What the plan may use as ingredients.

    Raw resources: INGREDIENT_USE_PERCENTAGE of stock. Craftable items: only the
    surplus above their own target, so stocking one item doesn't eat another's.
    """
    target_of = {t.item.name: t.target for t in targets}
    available: dict[str, int] = {}
    for name, amount in stock.items():
        item = ravenpy._items_name_data[name]
        if not item.craft_ingredients:
            available[name] = math.floor(amount * INGREDIENT_USE_PERCENTAGE)
        else:
            available[name] = max(0, amount - target_of.get(name, 0))
    return available


def _craft(plan: Plan, item: ravenpy.Item, amount: int, depth: int = 0) -> bool:
    if depth > 20:
        return False
    plan.crafts[item.name] += amount
    for ing in item.craft_ingredients:
        if not _consume(plan, ing.item, ing.amount * amount, depth + 1):
            return False
    return True


def _consume(plan: Plan, item: ravenpy.Item, amount: int, depth: int) -> bool:
    take = min(plan.available.get(item.name, 0), amount)
    if take:
        plan.available[item.name] -= take
        plan.taken[item.name] += take
        amount -= take
    if amount == 0:
        return True
    if not item.craft_ingredients:
        return False
    return _craft(plan, item, amount, depth)


def try_craft(plan: Plan, item: ravenpy.Item, amount: int) -> Plan | None:
    trial = copy.deepcopy(plan)
    return trial if _craft(trial, item, amount) else None


def plan_crafts(targets: list[Target], plan: Plan) -> Plan:
    """Fills each target's need, as far as resources allow, in priority order."""
    def priority(t: Target):
        if t.reason == "sales":
            return (0, -t.sales.orders, t.stock / max(1, t.sales.largest), t.item.name)
        return (1, 0, t.stock / max(1, t.target), t.item.name)

    for t in sorted(targets, key=priority):
        if t.need <= 0 or t.not_craftable:
            continue
        # Largest amount up to `need` that can be made (feasibility is monotonic).
        lo, hi, best = 1, t.need, None
        while lo <= hi:
            mid = (lo + hi) // 2
            trial = try_craft(plan, t.item, mid)
            if trial is not None:
                best, lo = (mid, trial), mid + 1
            else:
                hi = mid - 1
        if best is not None:
            t.crafted, plan = best
    return plan


def ordered_commands(crafts: dict[str, int]) -> list[str]:
    """Craft commands with every ingredient crafted before the items that use it."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        for ing in ravenpy._items_name_data[name].craft_ingredients:
            if ing.item.name in crafts:
                visit(ing.item.name)
        order.append(name)

    for name in sorted(crafts):
        visit(name)

    prefixes = {
        ravenpy.Skills.Crafting: "!craft",
        ravenpy.Skills.Cooking: "!cook",
        ravenpy.Skills.Alchemy: "!brew",
    }
    return [
        f"{prefixes.get(ravenpy._items_name_data[n].craft_skill, '!unknown')} {n} {crafts[n]}"
        for n in order
        if crafts[n] > 0
    ]


def report(targets: list[Target], plan: Plan) -> list[str]:
    rows = sorted(targets, key=lambda t: (t.reason != "sales", -t.sales.orders, t.item.name))
    rows = [t for t in rows if t.need > 0 or t.sales.orders > 0]
    name_w = max([len(t.item.name) for t in rows] + [4])
    header = (
        f"{'Item'.ljust(name_w)}  {'Orders':>6}  {'Largest':>7}  {'Stock':>7}  "
        f"{'Min':>5}  {'Target':>7}  {'Craft':>6}  Why"
    )
    lines = ["Targets:", header, "-" * len(header)]
    for t in rows:
        notes = [t.reason]
        if t.sales.orders and t.stock == 0:
            notes.append("sold out, demand may be higher")
        if t.not_craftable and t.need > 0:
            notes.append("not craftable, gather it")
        elif t.crafted < t.need:
            notes.append(f"short {t.need - t.crafted} (resources)")
        lines.append(
            f"{t.item.name.ljust(name_w)}  {t.sales.orders:>6}  {t.sales.largest:>7}  "
            f"{t.stock:>7}  {t.minimum:>5}  {t.target:>7}  {t.crafted:>6}  {', '.join(notes)}"
        )
    lines.append("")
    lines.append("Materials (give these to the crafter):")
    for name, amount in sorted(plan.taken.items()):
        lines.append(f"{str(amount).rjust(8)} {name}")
    lines.append("")
    lines.append("Commands:")
    lines.extend(ordered_commands(plan.crafts))
    return lines


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DB_PATH, help="Path to the bot's data.db")
    parser.add_argument("--channel", default=CHANNEL_ID, help="Channel id to stock")
    parser.add_argument(
        "--history-days", type=int, default=HISTORY_DAYS,
        help="Only count purchases from the last N days (default: all)",
    )
    args = parser.parse_args()

    rf = ravenpy.RavenNest(os.getenv("RAVENFALL_API_USER"), os.getenv("RAVENFALL_API_PASS"))
    await rf.login()
    await rf.refresh_items()

    try:
        prices = load_prices()
        sales = load_sales(args.db, args.history_days)
        stock = await load_stock(args.channel)
    finally:
        await close_session()

    targets = build_targets(stock, sales, prices)
    plan = plan_crafts(targets, Plan(available=starting_available(stock, targets)))
    lines = report(targets, plan)

    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    OUT_REQUIRED.write_text(json.dumps(dict(sorted(plan.taken.items())), indent=4), encoding="utf-8")

    needing = [t for t in targets if t.need > 0]
    print("\n".join(lines))
    print(
        f"\n{len(targets)} items have a target, {len(needing)} are below it, "
        f"{sum(1 for t in targets if t.crafted)} will be crafted."
    )
    print(f"Report written to {OUT_REPORT.resolve()}")
    print(f"Materials written to {OUT_REQUIRED.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
