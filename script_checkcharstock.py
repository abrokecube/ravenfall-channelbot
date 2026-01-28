import os
import math
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv
from ravenpy import ravenpy
from typing import Dict, List, Optional, Tuple

load_dotenv()
import bot.multichat_command as mc

CHANNEL_ID = "756734432"
CHAR_ID = "1253884011"
CHAR_NAME = "borkedcube"
IN_ITEM_LIST = "./output/root_ingredients.json"

async def main():
    item_list = json.loads(Path(IN_ITEM_LIST).read_text(encoding="utf-8"))
    rf = ravenpy.RavenNest(os.getenv("RAVENFALL_API_USER"), os.getenv("RAVENFALL_API_PASS"))
    await rf.login()
    await rf.refresh_items()

    char_items = await mc.get_char_items(CHANNEL_ID)
    john_crafter = None
    for char in char_items['data']:
        if char['twitch_id'] == CHAR_ID:
            john_crafter = char
            break
    if not john_crafter:
        print("Character not found")
        return

    out_commands = []
    for item in john_crafter['items']:
        item_data = ravenpy._items_id_data.get(item["id"])
        item_name = item_data.name
        if item_name in item_list:
            needed_amount = item_list[item_name]
            current_amount = item['amount']
            if current_amount >= needed_amount:
                # print(f"{item_name}: {current_amount} / {needed_amount} (Sufficient)")
                pass
            else:
                print(f"{item_name}: {current_amount} / {needed_amount} (Insufficient)")
                out_commands.append(
                    f"!giftto {CHAR_NAME} {item_name} {needed_amount - current_amount}"
                )
    if len(out_commands) == 0:
        print("All items are sufficiently stocked.")
        return
    commands_path = Path("./output/char_stock_commands.txt")
    commands_path.parent.mkdir(parents=True, exist_ok=True)
    commands_path.write_text("\n".join(out_commands), encoding="utf-8")
    print(f"\nGift commands written to {commands_path.resolve()}")

    
if __name__ == "__main__":
    asyncio.run(main())