import os
import math
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv
from ravenpy import ravenpy
from ravenpy.itemdefs import Items
from typing import Dict, List, Optional, Tuple

load_dotenv()
import bot.multichat_command as mc

CHANNEL_ID = "756734432"
CHAR_ID = "756734432"

SELL_PERCENTAGE = 0.5
ITEM_BLACKLIST = [
    Items.Coal,
    Items.GoldNugget,
    Items.GoldenLeaf,
]

def create_curve(points: List[Tuple[float, float]]) -> callable:
    """
    Create a curve function based on given points.
    Points should be a list of (input, output) tuples, sorted by input value.
    For values outside the defined range, the closest defined point's output is used.
    """
    # Sort points by x value
    points = sorted(points, key=lambda p: p[0])
    
    def curve_function(x: float) -> float:
        # Handle values below the first point
        if x <= points[0][0]:
            return points[0][1]
            
        # Handle values above the last point
        if x >= points[-1][0]:
            return points[-1][1]
            
        # Find the segment that contains x
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            
            if x1 <= x <= x2:
                # Linear interpolation
                if x1 == x2:  # Avoid division by zero
                    return y1
                # Calculate position between points (0 to 1)
                t = (x - x1) / (x2 - x1)
                return y1 + t * (y2 - y1)
                
        return points[-1][1]  # Shouldn't get here if points are valid
    
    return curve_function

INGREDIENT_CURVE = create_curve([
    (0, 0),
    (1,  1  * 1    ),
    (2,  2  * 1 ),
    (4,  4  * 1  ),
    (8,  8  * 1.1  ),
    (16, 16 * 1.2  ),
    (32, 32 * 1.3  ),
    (64, 64 * 1.5  ),
    (128, 128 * 1.7  ),
])

VALUE_CURVE = create_curve([
    (0,       1),           
    (1,       1),           
    (15,      10),           
    (100,     70),        
    (1000,    900),      
    (10000,   17500),    
    (50000,   125000),    
    (100000,  400000),    
    (1000000, 2000000),    
    (100000000, 100000000),    
    (float('inf'), 100000000) 
])

def get_fundamental_ingredients(item: ravenpy.Item) -> List[ravenpy.Ingredient]:
    # Recurse ingredients until the ingredient is not craftable
    ingredients_count = {}
    def recurse_ingredients(item: ravenpy.Item, amount: int = 1):
        for ing in item.craft_ingredients:
            if not ing.item.craft_ingredients:
                if ing.item.id not in ingredients_count:
                    ingredients_count[ing.item.id] = 0
                ingredients_count[ing.item.id] += ing.amount * amount
            recurse_ingredients(ing.item, amount * ing.amount)
    recurse_ingredients(item)
    
    ingredients = []
    for ingredient_id, amount in ingredients_count.items():
        ingredients.append(ravenpy.Ingredient(item=ravenpy._items_id_data[ingredient_id], amount=amount))
    return ingredients

new_blacklist = set()
for item in ITEM_BLACKLIST:
    new_blacklist.add(item.value)    
ITEM_BLACKLIST = new_blacklist

async def main():    
    base_item_values: dict[str, int] = {}
    with open("data/base_item_values.json", "r", encoding="utf-8") as f:
        base_item_values = json.load(f)

    item_values: dict[str, int] = {}
    for item in ravenpy.get_all_items():
        # Only consider items that have crafting ingredients
        sell_price = base_item_values.get(item.name, item.sell_price)
        item_values[item.name] = sell_price
        if not getattr(item, "craft_ingredients", None):
            continue
        if not item.craft_ingredients:
            continue
        fundamental_ingredients = get_fundamental_ingredients(item)

        total_ingredient_value = 0
        for ing in fundamental_ingredients:
            sell_price = base_item_values.get(ing.item.name, ing.item.sell_price)
            amount = ing.amount
            
            total_ingredient_value += sell_price * INGREDIENT_CURVE(amount)

        if total_ingredient_value > 0:
            item_values[item.name] = total_ingredient_value

    # Apply the value curve to all items
    for item in item_values:
        item_values[item] = int(VALUE_CURVE(item_values[item]))    
    
    char_items = await mc.get_char_items(CHANNEL_ID)
    seller = None
    for char in char_items['data']:
        if char['twitch_id'] == CHAR_ID:
            seller = char
            break
    if not seller:
        print("Character not found")
        return
    out_commands = []
    for item in seller['items']:
        if item['equipped'] or item["soulbound"]:
            continue
        item_data = ravenpy._items_id_data.get(item["id"])
        if not item_data:
            continue
        if item_data.id in ITEM_BLACKLIST:
            continue
        item_name = item_data.name
        sell_price = item_values.get(item_name, item_data.sell_price)
        amount = int(item['amount'] * SELL_PERCENTAGE)
        if amount < 1:
            continue
        print(f"Selling {amount}x {item_name} {sell_price} credits each")
        out_commands.append(
            f"!sell {item_name} {amount} {sell_price}"
        )
    if len(out_commands) == 0:
        print("No items to sell.")
        return
    out_commands = sorted(out_commands)
    commands_path = Path("./output/sell_commands.txt")
    commands_path.parent.mkdir(parents=True, exist_ok=True)
    commands_path.write_text("\n".join(out_commands), encoding="utf-8")
    print(f"\nSell commands written to {commands_path.resolve()}")

    
if __name__ == "__main__":
    asyncio.run(main())