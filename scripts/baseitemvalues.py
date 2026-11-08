import os
import asyncio
import json
from dotenv import load_dotenv
import ravenpy
from typing import List, Tuple, Optional

load_dotenv()

async def main():
    rf = ravenpy.RavenNest(os.getenv("RAVENFALL_API_USER"), os.getenv("RAVENFALL_API_PASS"))
    await rf.login()
    await rf.refresh_items()

    path = "data/base_item_values.json"
    existing: dict[str, int] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)

    base_item_values: dict[str, int] = {}
    for item in ravenpy.get_all_items():
        if not item.craft_ingredients:
            # A source value of 1 is a placeholder: keep the value already on file
            # A value of 0 on file means not redeemable: never overwrite it
            if existing.get(item.name) == 0:
                base_item_values[item.name] = 0
                print(f"{item.name}: kept 0, source price is {item.sell_price}")
            elif item.sell_price == 1 and item.name in existing:
                base_item_values[item.name] = existing[item.name]
            else:
                base_item_values[item.name] = item.sell_price

    with open("data/base_item_values.json", "w") as f:
        json.dump(base_item_values, f, indent=4)

if __name__ == "__main__":
    asyncio.run(main())
