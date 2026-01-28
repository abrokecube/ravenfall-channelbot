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

IN_FILE = "./output/planned_crafts copy.txt"
OUT_FILE = "./output/root_ingredients.json"

async def main():
    rf = ravenpy.RavenNest(os.getenv("RAVENFALL_API_USER"), os.getenv("RAVENFALL_API_PASS"))
    await rf.login()
    await rf.refresh_items()

    item_lines = Path(IN_FILE).read_text(encoding="utf-8").splitlines()
    item_list = defaultdict(int)

    for line in item_lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        if parts[-1].isdigit() == False:
            continue
        item_name = " ".join(parts[1:-1])
        item_amount = int(parts[-1])
        print(item_name, item_amount)
        item_list[item_name] += item_amount
    
    for item_name, amount in list(item_list.items()):
        item = ravenpy._items_name_data.get(item_name, None)
        if not item:
            print(f"Item not found: {item_name}")
            continue
        for ing in item.craft_ingredients:
            item_list[ing.item.name] -= ing.amount * amount
        del item_list[item_name]
    
    out_item_list = {item: -amount for item, amount in item_list.items() if amount < 0}
        
    out_path = Path(OUT_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_item_list, indent=4), encoding="utf-8")
    print(f"Wrote root ingredients to {out_path.resolve()}")

    
if __name__ == "__main__":
    asyncio.run(main())