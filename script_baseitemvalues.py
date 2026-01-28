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

    base_item_values: dict[str, int] = {}
    for item in ravenpy.get_all_items():
        if not item.craft_ingredients:
            base_item_values[item.name] = item.sell_price
    
    with open("data/base_item_values.json", "w") as f:
        json.dump(base_item_values, f, indent=4)

if __name__ == "__main__":
    asyncio.run(main())
