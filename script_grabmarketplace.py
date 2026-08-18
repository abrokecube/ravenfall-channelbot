# pyright: basic
import os
import asyncio
import json
from dotenv import load_dotenv
import ravenpy
from typing import Tuple, Optional
import csv

load_dotenv()
from utils.script_helpers import get_multichat_client

OUT_CSV_FILE = "./output/marketplace_items.csv"

async def main():
    rf = ravenpy.RavenNest(os.getenv("RAVENFALL_API_USER"), os.getenv("RAVENFALL_API_PASS"))
    await rf.login()

    multichat = get_multichat_client()
    chars = await multichat.get_char_info()
    char_ids = set([x.id for x in chars])

    market = await rf.get_marketplace()
    with open(OUT_CSV_FILE, "w", newline="", encoding="utf-8") as f:
        table = csv.writer(f)
        table.writerow(["Item Name", "Amount", "Price Per Item", "Total cost", "Buy command", "list time", "Seller ID"])
        for item in market:
            if item.seller_char_id in char_ids:
                continue
            buy_command = f"!buy {item.item.name} {item.amount} {item.price_per_item}"
            table.writerow([
                item.item.name,
                item.amount,
                item.price_per_item,
                item.amount * item.price_per_item,
                buy_command,
                item.created.isoformat(),
                item.seller_char_id,
            ])

if __name__ == "__main__":
    asyncio.run(main())
