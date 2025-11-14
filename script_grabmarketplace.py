import os
import asyncio
import json
from dotenv import load_dotenv
import ravenpy
from typing import List, Tuple, Optional
import csv

load_dotenv()
import bot.multichat_command as mc

OUT_CSV_FILE = "./output/marketplace_items.csv"

async def main():
    rf = ravenpy.RavenNest(os.getenv("API_USER"), os.getenv("API_PASS"))
    await rf.login()
    
    chars = await mc.get_char_info()
    char_ids = set([x["id"] for x in chars["data"]])
    
    market = await rf.get_marketplace()
    table = csv.writer(open(OUT_CSV_FILE, "w", newline='', encoding="utf-8"))
    table.writerow(["Item Name", "Amount", "Price Per Item", "Total cost", "Buy command", "List time", "Seller ID"])
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
    
asyncio.run(main())