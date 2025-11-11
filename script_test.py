import re
from ravenpy import ravenpy
from typing import Dict
import os
from dotenv import load_dotenv
load_dotenv()
from bot.multichat_command import get_char_items
import asyncio

CHANNEL_ID = "756734432"

def fill_whitespace(text: str, pattern: str = ". "):
    """
    Replace whitespace runs with a repeated pattern, keeping a single real space
    at each edge of the run. The total length of the run is preserved.

    Example:
        "a          b" -> "a . . . .  b"
    """
    def repl(m):
        run = m.group(0)
        run_len = len(run)
        if run_len <= 2:
            # Too short to fit pattern inside — leave as-is
            return run

        # Keep 1 space at each end
        inner_len = run_len - 2
        repeated = (pattern * ((inner_len // len(pattern)) + 1))[:inner_len]

        return " " + repeated + " "

    return re.sub(r' +', repl, text)

async def get_all_item_count(channel_id) -> Dict[str, int]:
    char_items = await get_char_items(channel_id)
    total_items = {}
    for user in char_items["data"]:
        for user_item in user["items"]:
            if user_item['equipped']:
                continue
            item = ravenpy._items_id_data.get(user_item["id"])
            if item is None:
                continue
            if item.name in total_items:
                total_items[item.name] += user_item["amount"]
            else:
                total_items[item.name] = user_item["amount"]
    return total_items

async def main():
    rf = ravenpy.RavenNest(os.getenv("API_USER"), os.getenv("API_PASS"))
    await rf.login()
    await rf.refresh_items()

    item_counts = await get_all_item_count(CHANNEL_ID)
    out_str = [
        "Stock list for channel: " + CHANNEL_ID,
        ""
    ]
    categories = {
        "Raw Materials": [],
        "Materials": [],
        "Armor": [],
        "Weapons": [],
        "Accessories": [],
        "Pets": [],
        "Food": [],
        "Potions": [],
        "Cosmetics": [],
        "Scrolls": [],
        "Other": [],
    }
    for item in ravenpy.get_all_items():
        if item.name not in item_counts:
            item_counts[item.name] = 0
    item_counts_list = sorted(list(item_counts.items()), key=lambda x: x[0])
    item_counts_list = sorted(item_counts_list, key=lambda x: getattr(ravenpy._items_name_data[x[0]].material, 'value', 0))
    item_counts_list = sorted(item_counts_list, key=lambda x: x[1] > 0, reverse=True)
    item_cols = 27
    num_cols = 6
    for item_name, count in item_counts_list:
        item = ravenpy._items_name_data[item_name]
        count = 0
        if item.name in item_counts:
            count = item_counts[item.name]
        item_str = f"{item.name.ljust(item_cols)} {str(count).rjust(max(0, min(num_cols, (item_cols+6) - len(item.name) )))}"
        item_str = fill_whitespace(item_str, ".")
        item_str = f"  {item_str}"
        if item.category == ravenpy.ItemCategory.Resource and len(item.used_in) > 0:
            if not item.craft_ingredients:
                categories["Raw Materials"].append(item_str)
            else:
                categories["Materials"].append(item_str)
        else:
            match item.category:
                case ravenpy.ItemCategory.Armor:
                    categories["Armor"].append(item_str)
                case ravenpy.ItemCategory.Weapon:
                    categories["Weapons"].append(item_str)
                case ravenpy.ItemCategory.Ring | ravenpy.ItemCategory.Amulet:
                    categories["Accessories"].append(item_str)
                case ravenpy.ItemCategory.Pet:
                    categories["Pets"].append(item_str)
                case ravenpy.ItemCategory.Food:
                    categories["Food"].append(item_str)
                case ravenpy.ItemCategory.Potion:
                    categories["Potions"].append(item_str)
                case ravenpy.ItemCategory.Cosmetic | ravenpy.ItemCategory.Skin:
                    categories["Cosmetics"].append(item_str)
                case ravenpy.ItemCategory.Scroll:
                    categories["Scrolls"].append(item_str)
                case _:
                    categories["Other"].append(item_str)
    for category_name, items in categories.items():
        if not items:
            continue
        out_str.append(f"{category_name} --- -- -- - -")
        out_str.extend(items)
        out_str.append("")        
    print("\n".join(out_str))
    
if __name__ == "__main__":
    asyncio.run(main())