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
INGREDIENT_USE_PERCENTAGE = 0.75
MIN_STOCK_COUNT = 0
MAX_STOCK_COUNT = 15000
CATEGORY_LIMITS = {
    ravenpy.ItemCategory.Weapon: 50,
    ravenpy.ItemCategory.Armor: 50,
    ravenpy.ItemCategory.Ring: 50,
    ravenpy.ItemCategory.Amulet: 50,
    ravenpy.ItemCategory.Food: 2500,
    ravenpy.ItemCategory.Potion: 2500,
    ravenpy.ItemCategory.Pet: 2500,
    ravenpy.ItemCategory.Resource: 15000,
    ravenpy.ItemCategory.StreamerToken: 2500,
    ravenpy.ItemCategory.Scroll: 2500,
    ravenpy.ItemCategory.Skin: 2500,
    ravenpy.ItemCategory.Cosmetic: 2500,
}

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
    (0,    0),
    (1,    1    ),
    (2,    2     ),
    (4,    4    ),
    (8,    6    ),
    (16,   14    ),
    (32,   28    ),
    (64,   32   ),
    (128,  50   ),
    (256,  60   ),
    (512,  70   ),
    (1024, 80   ),
    (2048, 90   ),
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


async def main():
    rf = ravenpy.RavenNest(os.getenv("API_USER"), os.getenv("API_PASS"))
    await rf.login()
    await rf.refresh_items()
    
    char_items = await mc.get_char_items(CHANNEL_ID)
    total_items: Dict[str, int] = {}
    for user in char_items["data"]:
        for user_item in user["items"]:
            if user_item['soulbound'] or user_item['equipped']:
                continue
            item = ravenpy._items_id_data.get(user_item["id"])
            if item is None:
                continue
            if item.name in total_items:
                total_items[item.name] += user_item["amount"]
            else:
                total_items[item.name] = user_item["amount"]
    
    item_base_ingredients: Dict[str, Dict[str, int]] = {}
    item_total_ingredient_count: Dict[str, Dict[str, int]] = {}
    item_max_stock: Dict[str, Dict[str, int]] = {}
    item_recipes: Dict[str, Dict[str, int]] = {}
    resources: Dict[str, int] = {}
    for item in ravenpy.get_all_items():
        if not item.craft_ingredients:
            resources[item.name] = total_items.get(item.name, 0) * INGREDIENT_USE_PERCENTAGE
            continue
        base_ings = {}
        total_ing_count = 0
        direct_recipe: Dict[str, int] = {}
        for ing in get_fundamental_ingredients(item):
            base_ings[ing.item.name] = ing.amount
            total_ing_count += ing.amount
        for ing in item.craft_ingredients:
            direct_recipe[ing.item.name] = ing.amount
        item_base_ingredients[item.name] = base_ings
        item_total_ingredient_count[item.name] = total_ing_count
        item_max_stock[item.name] = min(CATEGORY_LIMITS[item.category], MAX_STOCK_COUNT)
        item_recipes[item.name] = direct_recipe
    
    remaining_resources: Dict[str, int] = {
        name: max(0, math.floor(amount)) for name, amount in resources.items()
    }

    item_craft_counts: Dict[str, int] = {
        name: 0 for name in item_base_ingredients.keys()
    }

    def compute_priority(item_name: str) -> float:
        current_stock = total_items.get(item_name, 0) + item_craft_counts[item_name]
        stock_deficit = max(MIN_STOCK_COUNT - current_stock, 0)
        total_ingredients = item_total_ingredient_count[item_name]
        score = ((stock_deficit + 1) / (current_stock + 1)) / INGREDIENT_CURVE(total_ingredients)
        return score

    total_resource_count = 0
    for _, a in remaining_resources.items():
        total_resource_count += a
    remaining_resource_count = total_resource_count

    with tqdm(total=total_resource_count, unit="item") as pbar:
        while True:
            best_item: Optional[str] = None
            best_score = 0.0
            best_max_crafts = 0

            for item_name, ingredients in item_base_ingredients.items():
                current_stock = total_items.get(item_name, 0) + item_craft_counts[item_name]
                if current_stock > item_max_stock[item_name]:
                    continue

                max_crafts = math.inf
                for ingredient_name, ingredient_amount in ingredients.items():
                    if ingredient_amount <= 0:
                        continue
                    available = remaining_resources.get(ingredient_name, 0)
                    if available < ingredient_amount:
                        max_crafts = 0
                        break
                    max_crafts = min(max_crafts, available // ingredient_amount)

                if max_crafts <= 0:
                    continue

                score = compute_priority(item_name)
                if score > best_score or (
                    math.isclose(score, best_score) and max_crafts > best_max_crafts
                ):
                    best_item = item_name
                    best_score = score
                    best_max_crafts = int(max_crafts)

            if best_item is None:
                break
            
            icount = 0
            for ingredient_name, ingredient_amount in item_base_ingredients[best_item].items():
                remaining_resources[ingredient_name] -= ingredient_amount
                icount += ingredient_amount
            pbar.update(icount)
            item_craft_counts[best_item] += 1

    item_craft_counts = {
        name: crafted for name, crafted in item_craft_counts.items() if crafted > 0
    }

    planned_list = sorted(item_craft_counts.items())

    command_counts: Dict[str, int] = defaultdict(int)
    dependency_graph: Dict[str, List[str]] = defaultdict(list)

    def accumulate_all(item_name: str, amount: int) -> None:
        if amount <= 0:
            return
        command_counts[item_name] += amount
        recipe = item_recipes.get(item_name)
        if not recipe:
            return
        for ingredient_name, ingredient_amount in recipe.items():
            total_needed = ingredient_amount * amount
            if total_needed <= 0:
                continue
            dependency_graph[item_name].append(ingredient_name)
            accumulate_all(ingredient_name, total_needed)

    for item_name, crafted in planned_list:
        accumulate_all(item_name, crafted)

    ordered_items: List[str] = []
    visited: Dict[str, int] = {}

    def dfs(node: str) -> None:
        state = visited.get(node, 0)
        if state == 1:
            return
        if state == 2:
            return
        visited[node] = 1
        for child in dependency_graph.get(node, []):
            dfs(child)
        visited[node] = 2
        ordered_items.append(node)

    for item_name in command_counts.keys():
        dfs(item_name)

    ordered_items = [name for name in ordered_items if command_counts[name] > 0]

    print("Planned crafts:")
    for item_name in ordered_items:
        print(f"  {item_name}: {command_counts[item_name]}")

    base_materials: List[str] = []
    commands: List[str] = []
    for item_name in ordered_items:
        crafted = command_counts[item_name]
        item = ravenpy._items_name_data.get(item_name)
        if item is None:
            continue
        if not item.craft_ingredients:
            base_materials.append(f"{str(crafted).rjust(6)} {item_name}")
            continue
        skill = getattr(item, "craft_skill", None)
        if skill == ravenpy.Skills.Crafting:
            prefix = "!craft"
        elif skill == ravenpy.Skills.Cooking:
            prefix = "!cook"
        elif skill == ravenpy.Skills.Alchemy:
            prefix = "!brew"
        else:
            prefix = "!unknown"
        commands.append(f"{prefix} {item_name} {crafted}")

    out_text = []
    out_text.append("Materials:")
    out_text.extend(base_materials)
    out_text.append("\nCommands:")
    out_text.extend(commands)
    commands_path = Path("./output/planned_crafts.txt")
    commands_path.parent.mkdir(parents=True, exist_ok=True)
    commands_path.write_text("\n".join(out_text), encoding="utf-8")

    print(f"\nCraft commands written to {commands_path.resolve()}")

    # print("Remaining resources:")
    # for resource_name, amount in sorted(remaining_resources.items()):
    #     print(f"  {resource_name}: {amount}")

if __name__ == "__main__":
    asyncio.run(main())
