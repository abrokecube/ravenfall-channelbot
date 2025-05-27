import os
import math
import thefuzz.process
import thefuzz.fuzz
import json

dirname = os.path.dirname(__file__)

item_materials = {
    'Bronze': 1,
    'Iron': 2,
    'Steel': 3,
    'Black': 4,
    'Mithril': 5,
    'Adamantite': 6,
    'Rune': 7,
    'Dragon': 8,
    'Abraxas': 9,
    'Phantom': 10,
    'Lionsbane': 11,
    'Ether': 12,
    'Ancient': 13,
    'Atlarus': 14
}
skills = [
    "Attack",
    "Defense",
    "Strength",
    "Health",
    "Woodcutting",
    "Fishing",
    "Mining",
    "Crafting",
    "Cooking",
    "Farming",
    "Slayer",
    "Magic",
    "Ranged",
    "Sailing",
    "Healing",
    "Gathering",
    "Alchemy",
]
item_stat_names = [
    ("weaponAim", 0),
    ("weaponPower", 1),
    ("magicAim", 2),
    ("magicPower", 3),
    ("rangedAim", 4),
    ("rangedPower", 5),
    ("armorPower", 6),
]
item_requirement_names = [
    ("requiredAttackLevel", 0),
    ("requiredDefenseLevel", 1),
    ("requiredMagicLevel", 11),
    ("requiredRangedLevel", 12),
    ("requiredSlayerLevel", 10),
]


async def _fetch_raw_item_data(rf):
    with open(os.path.join(dirname, "data/internal_game_data.json")) as f:
        a = json.load(f)
    item_effects: dict = a['item_effects']
    item_raid_drops: dict = a['item_raid_drops']

    item_effects_strings = list(item_effects.keys())
    item_raid_drops_strings = list(item_raid_drops.keys())

    items = await rf._items()
    recipes = await rf._recipes()
    drops = await rf._drops()
    redeemables = await rf._redeemables()

    items_grouped = {}
    for item in items:
        items_grouped[item['id']] = [item, None, None, None]
    for recipe in recipes:
        items_grouped[recipe['itemId']][1] = recipe
    for drop in drops:
        items_grouped[drop['itemId']][2] = drop
    for redeemable in redeemables:
        items_grouped[redeemable['itemId']][3] = redeemable
    

    items_out = {}
    for item_id in items_grouped:
        item, recipe, drop, redeemable = items_grouped[item_id]
        item_a = {
            "id": item['id'],
            "name": item['name'],
            "description": item['description'],
            "stats": [],
            "level": item['level'],
            "equip_requirements": [],
            "type": item['type'],
            "category": item['category'],
            "material": item['material'],
            "sell_price": item['shopSellPrice'],
            "buy_price": item['shopBuyPrice'],
            "enchantments": 0,
            "soulbound": item['soulbound'],
            "modified": item['modified'],
            "craft_skill": None,
            "craft_level": 0,
            "min_success_rate": 0,
            "max_success_rate": 0,
            "preperation_time": 0,
            "is_fixed_success_rate": True,
            "craft_fail_item": None,
            "craft_ingredients": [],
            "drop_skill": None,
            "drop_level": 0,
            "drop_chance": 0,
            "drop_cooldown": 0,
            "used_in": [],
            "effects": [],
            "raid_drop_month_start": 0,
            "raid_drop_month_length": 0,
            "raid_min_drop": 0,
            "raid_max_drop": 0,
            "raid_drop_tier": 0,
            "drop_slayer_requirement": 0
        }
        if item_a['category'] in [0, 1, 11, 2, 3]:
            item_a['enchantments'] = max(1, math.floor(math.floor(item_a['level'] / 10) / 5))
        for key, name in item_stat_names:
            value = item[key]
            if value > 0:
                item_a['stats'].append({
                    'stat': name,
                    'level': value
                })
        for key, name in item_requirement_names:
            value = item[key]
            if value > 0:
                item_a['equip_requirements'].append({
                    'skill': name,
                    'level': value
                })
        items_out[item['id']] = item_a
        if recipe:
            item_a['craft_skill'] = skills[recipe['requiredSkill']]
            item_a['craft_level'] = recipe['requiredLevel']
            item_a['min_success_rate'] = recipe['minSuccessRate']
            item_a['max_success_rate'] = recipe['maxSuccessRate']
            item_a['preperation_time'] = recipe['preparationTime']
            item_a['is_fixed_success_rate'] = recipe['fixedSuccessRate']
            item_a['craft_fail_item'] = recipe['failedItemId']
            for ingredient in recipe['ingredients']:
                item_a['craft_ingredients'].append({
                    'item_id': ingredient['itemId'],
                    'amount': ingredient['amount']
                })
        if drop:
            item_a['drop_skill'] = skills[drop['requiredSkill']]
            item_a['drop_level'] = drop['levelRequirement']
            item_a['drop_chance'] = drop['dropChance']
            item_a['drop_cooldown'] = drop['cooldown']

        if item_a["category"] in [4, 5]:
            found_effect_str = thefuzz.process.extract(item_a['name'], item_effects_strings, limit=1, scorer=thefuzz.fuzz.ratio)[0]
            if found_effect_str[1] > 90:
                item_a['effects'] = item_effects[found_effect_str[0]]['effects']
            else:
                ...
        found_raid_drop_str = thefuzz.process.extract(item_a['name'], item_raid_drops_strings, limit=1, scorer=thefuzz.fuzz.ratio)[0]
        if found_raid_drop_str[1] > 90:
            raid_stuff = item_raid_drops[found_raid_drop_str[0]]
            item_a["raid_drop_month_start"] = raid_stuff["month_start"]
            item_a["raid_drop_month_length"] = raid_stuff["months_length"]
            item_a["raid_min_drop"] = raid_stuff["min_drop"]
            item_a["raid_max_drop"] = raid_stuff["max_drop"]
            item_a["raid_drop_tier"] = raid_stuff["tier"]
            item_a["drop_slayer_requirement"] = raid_stuff["slayer_requirement"]
        else:
            ...
        if item_a['material'] == 0 and item_a['category'] in [0, 1]:
            first_word = item_a['name'].split(" ")[0]
            result_mat_id = item_materials.get(first_word)
            if result_mat_id:
                print(f"Assigning {item_a['name']} {first_word} material")
                item_a['material'] = result_mat_id

    for item_id in items_out:
        item = items_out[item_id]
        for ingredient in item['craft_ingredients']:
            items_out[ingredient['item_id']]['used_in'].append(item['id'])

    items_list = []
    for item_id, item in items_out.items():
        items_list.append(item)
    items_list.sort(key=lambda x: x['name'])

    return items_list
