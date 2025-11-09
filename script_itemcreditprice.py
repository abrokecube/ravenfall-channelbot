import os
import asyncio
import json
from dotenv import load_dotenv
import ravenpy
from typing import List, Tuple, Optional

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
    (2,  2  * 0.7 ),
    (4,  4  * 0.6  ),
    (8,  8  * 0.5  ),
    (16, 16 * 0.4  ),
    (32, 32 * 0.3  ),
    (64, 64 * 0.25  ),
    (128, 128 * 0.2  ),
])

VALUE_CURVE = create_curve([
    (0,       1),           
    (1,       1),           
    (15,      8),           
    (100,     30),        
    (1000,    200),      
    (10000,   800),    
    (50000,   1500),    
    (100000,  3000),    
    (1000000, 20000),    
    (100000000, 2000000),    
    (float('inf'), 2000000) 
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
        ingredients.append(ravenpy.Ingredient(item=ravenpy.ravenpy._items_id_data[ingredient_id], amount=amount))
    return ingredients

load_dotenv()

base_item_values: dict[str, int] = {}
with open("data/base_item_values.json", "r", encoding="utf-8") as f:
    base_item_values = json.load(f)

async def main():
    rf = ravenpy.RavenNest(os.getenv("API_USER"), os.getenv("API_PASS"))
    await rf.login()
    await rf.refresh_items()
    # Compute values for craftable items: 95% of the sum of ingredient sell prices
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

        if total_ingredient_value > 0 and total_ingredient_value < sell_price:
            item_values[item.name] = total_ingredient_value

    # Apply the value curve to all items
    for item in item_values:
        item_values[item] = int(VALUE_CURVE(item_values[item]))
    
    out_path = os.path.join("data", f"item_values.json")

    # Write JSON with compact formatting
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(item_values, f, ensure_ascii=False, separators=(",", ":"), indent=2)

    print(f"Wrote {len(item_values)} item values to {out_path}")

asyncio.run(main())