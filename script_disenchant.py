# pyright: basic
# ruff: noqa: CPY001, T201
"""Generate ``?say -as:<user> !disenchant <full item name>`` commands.

Finds every enchanted copy of an item across all characters in a town.
Requires the ravenfall-multichat server to expose item enchantments.
"""
import argparse
import asyncio
import math
from pathlib import Path

from dotenv import load_dotenv
from ravenpy import ravenpy

load_dotenv()
from utils.script_helpers import get_multichat_client, get_ravenfall_credentials

OUT_FILE = "./output/disenchant_commands.txt"


def enchanted_name(base_name: str, enchantments) -> str:
    """Build the in-game name, e.g. "Atlarus Helmet of Cooking +104".

    Mirrors RavenNest ``EnchantmentManager.GetEnchantedName``: the highest
    attribute (first wins ties) names it, and the number is the sum of every
    attribute's floored value.
    """
    top = max(enchantments, key=lambda e: e.percentage)  # max() keeps first on ties
    total = sum(math.floor(e.percentage) for e in enchantments)
    return f"{base_name} of {top.stat.capitalize()} +{total}"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate ?say -as:<user> !disenchant commands for enchanted "
        "copies of an item in all characters' inventories in a town."
    )
    parser.add_argument("item", help="Base item name, e.g. 'Atlarus Helmet'")
    parser.add_argument("town", help="Town (channel) name or id")
    args = parser.parse_args()

    username, password = get_ravenfall_credentials()
    rf = ravenpy.RavenNest(username, password)
    await rf.login()
    await rf.refresh_items()

    item_data = next(
        (
            i
            for i in ravenpy.get_all_items()
            if i.name.lower() == args.item.strip().lower()
        ),
        None,
    )
    if item_data is None:
        print(f"Unknown item: {args.item}")
        return 1

    multichat = get_multichat_client()
    town = args.town.strip().lower()
    channel_id = None
    for info in await multichat.get_char_info():
        if info.channel_id and town in (info.channel_id, (info.channel_name or "").lower()):
            channel_id = info.channel_id
            break
    if channel_id is None:
        print(f"Town not found: {args.town}")
        return 1

    commands: list[str] = []
    seen: set[str] = set()
    for char in await multichat.get_char_items(channel_id):
        for item in char.items:
            if item.id != item_data.id or not item.enchantments:
                continue
            name = enchanted_name(item_data.name, item.enchantments)
            command = f"?say -as:{char.user_name} !disenchant {name}"
            if command in seen:
                continue
            seen.add(command)
            commands.append(command)

    if not commands:
        print(f"No enchanted {item_data.name} found in {args.town}.")
        return 1

    out_path = Path(OUT_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(commands), encoding="utf-8")
    print("\n".join(commands))
    print(f"\nWrote {len(commands)} commands to {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
