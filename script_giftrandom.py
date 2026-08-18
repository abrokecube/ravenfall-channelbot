# pyright: basic
import argparse
import asyncio
import random
from pathlib import Path

from dotenv import load_dotenv
from ravenpy import ravenpy

from utils.script_helpers import get_multichat_client, get_ravenfall_credentials

load_dotenv()

OUT_FILE = "./output/gift_commands.txt"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate !gift commands from a giver's inventory to random characters in a town."
    )
    parser.add_argument("giver_user", help="Twitch username of the giver")
    parser.add_argument(
        "giver_char_index", type=int, help="Character index of the giver (1-based)"
    )
    parser.add_argument(
        "town", help="Channel/town id to get the list of controllable characters for"
    )
    args = parser.parse_args()

    username, password = get_ravenfall_credentials()
    rf = ravenpy.RavenNest(username, password)
    await rf.login()
    await rf.refresh_items()

    multichat = get_multichat_client()
    chars = await multichat.get_char_items(args.town)

    giver = None
    for char in chars:
        if (
            char.user_name.lower() == args.giver_user.lower()
            and char.char_index == args.giver_char_index
        ):
            giver = char
            break
    if giver is None:
        print(
            f"Giver not found: {args.giver_user} (char index {args.giver_char_index}) "
            f"in town {args.town}"
        )
        return 1

    recipients = sorted(
        {
            char.user_name
            for char in chars
            if char.user_name.lower() != args.giver_user.lower()
        }
    )
    if not recipients:
        print("No recipients found in the town.")
        return 1

    commands = []
    for item in giver.items:
        if item.equipped or item.soulbound or item.amount <= 0:
            continue
        item_data = ravenpy._items_id_data.get(item.id)
        if item_data is None:
            continue
        recipient = random.choice(recipients)
        commands.append(f"!gift {recipient} {item_data.name} {item.amount}")

    if not commands:
        print("No giftable items found.")
        return 1

    out_path = Path(OUT_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(commands), encoding="utf-8")
    print(f"Wrote {len(commands)} gift commands to {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
