# pyright: basic
from typing import Any

import os
import asyncio
import json
from dotenv import load_dotenv
import ravenpy
from utils.format_time import format_seconds, TimeSize

load_dotenv()

async def main():
    rf = ravenpy.RavenNest(os.getenv("RAVENFALL_API_USER", ""), os.getenv("RAVENFALL_API_PASS", ""))
    await rf.login()
    await rf.refresh_items()

    out_text: list[str] = []
    all_items = ravenpy.get_all_items()
    all_items.sort(key=lambda x: x.drop_cooldown if x.drop_cooldown else 999)
    all_items.sort(key=lambda x: x.type.value if x.type else 999)
    for item in all_items:
        if item.drop_cooldown:
            out_text.append(f"{item.type.name},{item.name},{format_seconds(item.drop_cooldown, size=TimeSize.SMALL_SPACES)}")
    with open("./output/item_cooldowns.csv", "w") as f:
        f.write("\n".join(out_text))
    
    
if __name__ == "__main__":
    asyncio.run(main())
