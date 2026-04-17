from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast, override

import aiohttp
import thefuzz
import thefuzz.fuzz
import thefuzz.process
from anyio import Path as AsyncPath
from async_lru import alru_cache

from .enums import (
    ClanRole,
    ClanSkill,
    Effects,
    Enchantments,
    Islands,
    ItemCategory,
    ItemMaterials,
    ItemTypes,
    Skills,
    Stat,
)

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from . import itemdefs
    from .modals import (
        CraftIngredientJson,
        FuzzResult,
        InternalItemData,
        RFItemDropJson,
        RFItemJson,
        RFItemRedeemableJson,
        RFRecipeJson,
    )

dirname = Path(__file__).parent

item_materials = {
    "Bronze": 1,
    "Iron": 2,
    "Steel": 3,
    "Black": 4,
    "Mithril": 5,
    "Adamantite": 6,
    "Rune": 7,
    "Dragon": 8,
    "Abraxas": 9,
    "Phantom": 10,
    "Lionsbane": 11,
    "Ether": 12,
    "Ancient": 13,
    "Atlarus": 14,
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


class ItemEffectJSON(TypedDict):
    """Item effect as defined in the internal game data json."""

    id: int
    duration: int
    percentage: float
    min_amount: int


class ItemEffectsJSON(TypedDict):
    """List of item effects as defined in the internal game data json."""

    name: str
    effects: list[ItemEffectJSON]


class ItemRaidDropJSON(TypedDict):
    """Raid drop data as defined in the internal game data json."""

    name: str
    month_start: int
    months_length: int
    min_drop: float
    max_drop: float
    tier: int
    slayer_requirement: int


class InternalGameData(TypedDict):
    """Extracted internal game data."""

    item_effects: dict[str, ItemEffectsJSON]
    item_raid_drops: dict[str, ItemRaidDropJSON]


class Buh(TypedDict):
    """Grouped item data."""

    item: RFItemJson
    recipe: RFRecipeJson | None
    drop: RFItemDropJson | None
    redeemable: RFItemRedeemableJson | None


async def _fetch_raw_item_data(rf: RavenNest):
    f = await AsyncPath(dirname, "data/internal_game_data.json").open("r")
    a: InternalGameData = cast("InternalGameData", json.loads(await f.read()))
    item_effects: dict[str, ItemEffectsJSON] = a["item_effects"]
    item_raid_drops: dict[str, ItemRaidDropJSON] = a["item_raid_drops"]

    item_effects_strings = list(item_effects.keys())
    item_raid_drops_strings = list(item_raid_drops.keys())

    items = await rf._items()
    recipes = await rf._recipes()
    drops = await rf._drops()
    redeemables = await rf._redeemables()

    items_grouped: dict[str, Buh] = {}
    for item in items:
        items_grouped[item["id"]] = {
            "item": item,
            "recipe": None,
            "drop": None,
            "redeemable": None,
        }
    for recipe in recipes:
        items_grouped[recipe["itemId"]]["recipe"] = recipe
    for drop in drops:
        items_grouped[drop["itemId"]]["drop"] = drop
    for redeemable in redeemables:
        items_grouped[redeemable["itemId"]]["redeemable"] = redeemable

    items_out: dict[str, InternalItemData] = {}
    for item_id in items_grouped.keys():
        item_data = items_grouped[item_id]
        item = item_data["item"]
        recipe = item_data["recipe"]
        drop = item_data["drop"]
        redeemable = item_data["redeemable"]
        item_a: InternalItemData = {
            "id": item["id"],
            "name": item["name"],
            "description": item["description"],
            "stats": [],
            "level": item["level"],
            "equip_requirements": [],
            "type": item["type"],
            "category": item["category"],
            "material": item["material"],
            "sell_price": item["shopSellPrice"],
            "buy_price": item["shopBuyPrice"],
            "enchantments": 0,
            "soulbound": item["soulbound"],
            "modified": item["modified"],
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
            "drop_slayer_requirement": 0,
        }
        if item_a["category"] in [0, 1, 11, 2, 3]:
            item_a["enchantments"] = max(
                1, math.floor(math.floor(item_a["level"] / 10) / 5)
            )
        for key, name in item_stat_names:
            value = cast("int", item[key])
            if value > 0:
                item_a["stats"].append({"stat": name, "level": value})
        for key, name in item_requirement_names:
            value = cast("int", item[key])
            if value > 0:
                item_a["equip_requirements"].append({"skill": name, "level": value})
        items_out[item["id"]] = item_a
        if recipe:
            item_a["craft_skill"] = skills[recipe["requiredSkill"]]
            item_a["craft_level"] = recipe["requiredLevel"]
            item_a["min_success_rate"] = recipe["minSuccessRate"]
            item_a["max_success_rate"] = recipe["maxSuccessRate"]
            item_a["preperation_time"] = recipe["preparationTime"]
            item_a["is_fixed_success_rate"] = recipe["fixedSuccessRate"]
            item_a["craft_fail_item"] = recipe["failedItemId"]
            for ingredient in recipe["ingredients"]:
                item_a["craft_ingredients"].append(
                    {"item_id": ingredient["itemId"], "amount": ingredient["amount"]}
                )
        if drop:
            item_a["drop_skill"] = skills[drop["requiredSkill"]]
            item_a["drop_level"] = drop["levelRequirement"]
            item_a["drop_chance"] = drop["dropChance"]
            item_a["drop_cooldown"] = drop["cooldown"]

        _min_match_score = 90

        if item_a["category"] in [4, 5]:
            found_effect_str: FuzzResult = cast(
                "FuzzResult",
                thefuzz.process.extract(  # pyright: ignore[reportUnknownMemberType]
                    item_a["name"],
                    item_effects_strings,
                    limit=1,
                    scorer=thefuzz.fuzz.ratio,  # pyright: ignore[reportUnknownMemberType]
                )[0],
            )
            if found_effect_str[1] > _min_match_score:
                item_a["effects"] = item_effects[found_effect_str[0]]["effects"]
            else:
                ...
        found_raid_drop_str: FuzzResult = cast(
            "FuzzResult",
            thefuzz.process.extract(  # pyright: ignore[reportUnknownMemberType]
                item_a["name"],
                item_raid_drops_strings,
                limit=1,
                scorer=thefuzz.fuzz.ratio,  # pyright: ignore[reportUnknownMemberType]
            )[0],
        )
        if found_raid_drop_str[1] > _min_match_score:
            raid_stuff = item_raid_drops[found_raid_drop_str[0]]
            item_a["raid_drop_month_start"] = raid_stuff["month_start"]
            item_a["raid_drop_month_length"] = raid_stuff["months_length"]
            item_a["raid_min_drop"] = raid_stuff["min_drop"]
            item_a["raid_max_drop"] = raid_stuff["max_drop"]
            item_a["raid_drop_tier"] = raid_stuff["tier"]
            item_a["drop_slayer_requirement"] = raid_stuff["slayer_requirement"]

        if item_a["material"] == 0 and item_a["category"] in [0, 1]:
            first_word = item_a["name"].split(" ")[0]
            result_mat_id = item_materials.get(first_word)
            if result_mat_id:
                LOGGER.warning(f"Assigning {item_a['name']} {first_word} material")
                item_a["material"] = result_mat_id

    for item_id in items_out:
        item = items_out[item_id]
        for ingredient in item["craft_ingredients"]:
            items_out[ingredient["item_id"]]["used_in"].append(item["id"])

    items_list: list[InternalItemData] = list(items_out.values())
    items_list.sort(key=lambda x: x["name"])

    return items_list


class ItemEffect:
    """Effect of an item when used."""

    def __init__(self, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.effect: Effects = kwargs.get("effect", Effects.NoEffect)
        self.duration: float = kwargs.get("duration", 0.0)
        self.percentage: float = kwargs.get("percentage", 0.0)
        self.min_amount: float = kwargs.get("min_amount", 0.0)

    @override
    def __repr__(self):
        return (
            f"ItemEffect({self.effect.name}, {self.duration}s, "
            f"{self.percentage}, {self.min_amount})"
        )


class ItemRequirement:
    """Requirement for an item to be equipped."""

    def __init__(self, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.skill: Skills = kwargs.get("skill", Skills(0))
        self.level: int = kwargs.get("level", 0)

    @override
    def __repr__(self):
        return f"ItemRequirement({self.skill.name}, {self.level})"


class ItemStat:
    """Stat of an item."""

    def __init__(self, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.stat: Stat = kwargs.get("stat", Stat(0))
        self.level: int = kwargs.get("level", 0)

    @override
    def __repr__(self):
        return f"ItemStat({self.stat.name}, {self.level})"


class Ingredient:
    """An ingredient for crafting an item."""

    def __init__(self, item: Item, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.item: Item = item
        self.amount: int = kwargs.get("amount", 0)

    @override
    def __repr__(self):
        return f"Ingredient({self.item.name}, {self.amount})"


class Item:
    """An item in the game, with all its data and relations to other items."""

    def __init__(self, data: InternalItemData):
        self.id: str = data.get("id", "")
        self.name: str = data.get("name", "")
        self.description: str | None = data.get("description", None)
        self.level: int = data.get("level", 0)
        self.type: ItemTypes | None = (
            ItemTypes(data.get("type")) if data.get("type") else None
        )
        self.category: ItemCategory | None = ItemCategory(data.get("category"))
        self.material: ItemMaterials | None = (
            ItemMaterials(data.get("material")) if data.get("material") else None
        )
        self.sell_price: int = data.get("sell_price", 0)
        self.buy_price: int = data.get("buy_price", 0)
        self.enchantments: int = data.get("enchantments", 0)
        self.soulbound: bool = data.get("soulbound", False)
        self.craft_skill: Skills | None = _getenum_or_none(data["craft_skill"], Skills)
        self.craft_level: int = data.get("craft_level", 0)
        self.min_success_rate: float = data.get("min_success_rate", 0.0)
        self.max_success_rate: float = data.get("max_success_rate", 0.0)
        self.preparation_time: int = data.get("preperation_time", 0)
        self.is_fixed_success_rate: bool = data.get("is_fixed_success_rate", False)
        self.drop_skill: Skills | None = _getenum_or_none(data["drop_skill"], Skills)
        self.drop_level: int = data.get("drop_level", 0)
        self.drop_chance: float = data.get("drop_chance", 0.0)
        self.drop_cooldown: int = data.get("drop_cooldown", 0)
        self.raid_drop_month_start: int = data.get("raid_drop_month_start", 0)
        self.raid_drop_month_length: int = data.get("raid_drop_month_length", 0)
        self.raid_min_drop: float = data.get("raid_min_drop", 0)
        self.raid_max_drop: float = data.get("raid_max_drop", 0)
        self.raid_drop_tier: int = data.get("raid_drop_tier", 0)
        self.drop_slayer_requirement: int = data.get("drop_slayer_requirement", 0)

        self._craft_fail_item: str | None = data.get("craft_fail_item", None)
        self._craft_ingredients: list[CraftIngredientJson] = data.get(
            "craft_ingredients", []
        )
        self._used_in: list[str] = data.get("used_in", [])
        self._modified: str | None = data["modified"]

        self.craft_fail_item: Item | None = None
        self.craft_ingredients: list[Ingredient] = []
        self.used_in: list[Item] = []

        self.effects: list[ItemEffect] = []
        effects: list[dict[str, int | float]] = cast(
            "list[dict[str, int | float]]", data.get("effects", [])
        )
        for effect in effects:
            self.effects.append(
                ItemEffect(
                    effect=Effects(effect["id"]),
                    duration=effect["duration"],
                    percentage=effect["percentage"],
                    min_amount=effect["min_amount"],
                )
            )

        self.equip_requirements: list[ItemRequirement] = []
        reqs: list[dict[str, int]] = cast(
            "list[dict[str, int]]", data.get("equip_requirements", [])
        )
        for req in reqs:
            self.equip_requirements.append(
                ItemRequirement(skill=Skills(req["skill"]), level=req["level"])
            )

        self.stats: list[ItemStat] = []
        stats: list[dict[str, int]] = cast("list[dict[str, int]]", data.get("stats", []))
        for stat in stats:
            self.stats.append(ItemStat(stat=Stat(stat["stat"]), level=stat["level"]))

    @override
    def __eq__(self, value: object):
        return isinstance(value, Item) and self.id == value.id

    @override
    def __hash__(self):
        return hash(self.id)

    @override
    def __repr__(self):
        return f"Item({self.name}, {self.id})"


class CharacterStat:
    """A character's stat in the game."""

    def __init__(self, skill: Skills, exp: int, level: int):
        self.skill: Skills = skill
        self.level: int = level
        self.level_exp: int = exp
        self.total_exp_for_level: float = experience_for_level(level + 1)
        self.enchant_percent: float = 0
        self.enchant_levels: int = 0

    def _add_enchant(self, percent: float):
        self.enchant_percent += percent
        self.enchant_levels = round(self.level * self.enchant_percent)

    @override
    def __repr__(self):
        return (
            f"CharacterStat({self.skill.name}, {self.level}, "
            f"{self.level_exp}xp, {self.enchant_percent}%, {self.enchant_levels})"
        )


_ISO_EPOCH = "1970-01-01T00:00:00.000Z"


class ClanStat:
    """A clan's stat in the game."""

    def __init__(self, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.skill: ClanSkill = ClanSkill[
            cast("str", kwargs.get("name", "")).capitalize()
        ]
        self.level: int = kwargs.get("level", 0)
        self.experience: int = kwargs.get("experience", 0)
        self.max_level: int = kwargs.get("maxLevel", 0)

    @override
    def __repr__(self):
        return f"ClanStat({self.skill.name}, {self.level}, {self.experience}xp)"


class CharacterClanRole:
    """A character's role in their clan."""

    def __init__(self, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.role: ClanRole = ClanRole(kwargs.get("level"))
        self.joined: datetime = _parse_time(cast("str", kwargs.get("joined", _ISO_EPOCH)))

    @override
    def __repr__(self):
        return f"CharacterClanRole({self.role.name}, joined {self.joined.isoformat()})"


class CharacterClan:
    """A character's clan in the game."""

    def __init__(self, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.id: str = kwargs.get("id", "")
        self.owner_twitch_id: str = kwargs.get("owner", "")
        self.owner_id: str = kwargs.get("ownerUserId", "")
        self.level: int = kwargs.get("level", 0)
        self.experience: int = kwargs.get("experience", 0)
        self.name: str = kwargs.get("name", "")
        self.logo: str = kwargs.get("logo", "")
        self.skills: list[ClanStat] = []
        skills: list[dict[str, str | int]] = cast(
            "list[dict[str, str | int]]", kwargs.get("clanSkills", [])
        )
        if skills:
            for skill in skills:
                self.skills.append(ClanStat(**skill))

    @override
    def __repr__(self):
        return f"CharacterClan({self.name}, level {self.level}, {self.experience}xp)"


class ItemEnchantment:
    """An enchantment that can be applied to an item."""

    def __init__(self, enchant_string: str):
        stat, percentage = enchant_string.split(":")
        self.percentage: float = float(percentage.rstrip("%")) / 100
        self.stat: Enchantments = Enchantments[stat.capitalize()]

    @override
    def __repr__(self):
        return f"ItemEnchantment({self.stat.name}, {self.percentage * 100}%)"


class CharacterItem:
    """An item in a character's inventory."""

    def __init__(self, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.item: Item = _items_id_data[kwargs.get("itemId", "")]
        self.amount: int = kwargs.get("amount", 0)
        self.equipped: bool = kwargs.get("equipped", False)
        self.soulbound: bool = kwargs.get("soulbound", False)
        enchantment: str = cast("str", kwargs.get("enchantment", ""))
        self.enchantments: list[ItemEnchantment] = []
        self.active: bool = False
        if enchantment:
            for item in enchantment.split(";"):
                self.enchantments.append(ItemEnchantment(item))

    @override
    def __repr__(self):
        return (
            f"CharacterItem({self.item.name}, x{self.amount}, "
            f"equipped={self.equipped}, soulbound={self.soulbound}, "
            f"enchantments={self.enchantments}, active={self.active})"
        )


class CharacterStatusEffect:
    """A status effect that a character has."""

    def __init__(self, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.effect: Effects = Effects(kwargs.get("type"))
        self.amount: float = kwargs.get("amount", 0.0)
        self.duration: float = kwargs.get("duration", 0.0)
        self.time_left: float = kwargs.get("timeLeft", 0.0)
        self.start_time: datetime = _parse_time(
            cast("str", kwargs.get("startUtc", _ISO_EPOCH))
        )
        self.expires: datetime = _parse_time(
            cast("str", kwargs.get("expiresUtc", _ISO_EPOCH))
        )

    @override
    def __repr__(self):
        return (
            f"CharacterStatusEffect({self.effect.name}, {self.amount}, "
            f"{self.duration}s, {self.time_left}s left)"
        )


class CharacterEquipment:
    """A character's equipped items."""

    def __init__(self, equipment: list[CharacterItem]):
        self.helmet: CharacterItem | None = None
        self.chest: CharacterItem | None = None
        self.gloves: CharacterItem | None = None
        self.leggings: CharacterItem | None = None
        self.boots: CharacterItem | None = None
        self.ring: CharacterItem | None = None
        self.amulet: CharacterItem | None = None
        self.staff: CharacterItem | None = None
        self.weapon: CharacterItem | None = None  # melee weapon
        self.bow: CharacterItem | None = None
        self.pet: CharacterItem | None = None
        self.shield: CharacterItem | None = None

        for item in equipment:
            if not item.equipped:
                continue
            item.active = True
            match item.item.type:
                case ItemTypes.TwoHandedSword | ItemTypes.OneHandedSword:
                    self.weapon = item
                case ItemTypes.TwoHandedAxe | ItemTypes.OneHandedAxe:
                    self.weapon = item
                case ItemTypes.TwoHandedSpear:
                    self.weapon = item
                case ItemTypes.TwoHandedStaff:
                    self.staff = item
                case ItemTypes.TwoHandedBow:
                    self.bow = item
                case ItemTypes.Helmet:
                    self.helmet = item
                case ItemTypes.Chest:
                    self.chest = item
                case ItemTypes.Gloves:
                    self.gloves = item
                case ItemTypes.Boots:
                    self.boots = item
                case ItemTypes.Leggings:
                    self.leggings = item
                case ItemTypes.Shield:
                    self.shield = item
                case ItemTypes.Ring:
                    self.ring = item
                case ItemTypes.Amulet:
                    self.amulet = item
                case ItemTypes.Pet:
                    self.pet = item
                case _:
                    pass

        if (
            self.weapon
            and self.shield
            and self.weapon.item.type
            in {
                ItemTypes.TwoHandedSword,
                ItemTypes.TwoHandedAxe,
                ItemTypes.TwoHandedSpear,
            }
        ):
            self.shield.active = False

    def __iter__(self) -> Iterator[CharacterItem]:
        out = [
            self.helmet,
            self.chest,
            self.gloves,
            self.leggings,
            self.boots,
            self.ring,
            self.amulet,
            self.staff,
            self.weapon,
            self.bow,
            self.pet,
            self.shield,
        ]
        return [x for x in out if x].__iter__()

    @override
    def __repr__(self):
        return "CharacterEquipment"


fighting_replacements = {
    "Atk": "Attack",
    "Att": "Attack",
    "Heal": "Healing",
    "Def": "Defense",
    "Str": "Strength",
}


def _parse_time(iso_str: str):
    s = ""
    if iso_str[-1] == "Z":
        s = iso_str[:-1] + "+00:00"
    else:
        s = iso_str + "+00:00"
    return datetime.fromisoformat(s)


def _class_or_none[T](_obj: dict[str, Any] | None, _class: Callable[..., T]) -> T | None:  # pyright: ignore [reportExplicitAny]
    if _obj is not None:
        return _class(**_obj)
    return None


def _getenum_or_none[E: Enum](prop: str | None, enum: type[E]) -> E | None:
    if prop is not None:
        return enum[prop]
    return None


def _call_or_none[T, V](_obj: T | None, _callable: Callable[[T], V]) -> V | None:
    if _obj is not None:
        return _callable(_obj)
    return None


class Character:
    """A character in the game, with all its data and relations to other entities."""

    def __init__(self, data: dict[str, Any]):  # pyright: ignore [reportExplicitAny]
        self._raw: dict[str, Any] = data  # pyright: ignore [reportExplicitAny]
        self.time_recieved: datetime = datetime.now(UTC)
        self.id: str = data["id"]
        self.char_id: str = self.id
        self.user_id: str = data["userId"]
        self.user_name: str = data["userName"]
        self.twitch_id: str = data["twitch"]["platformId"]
        self.identifier: str = data["identifier"]
        self.character_index: int = data["characterIndex"] + 1
        self.index: int = self.character_index
        if not self.identifier:
            self.identifier = str(self.character_index)
        self.name: str = self.identifier
        self.patreon_tier: int = data["patreonTier"]
        self.is_hidden_in_highscore: bool = data["isHiddenInHighscore"]
        self.coins: int = data["resources"]["coins"]

        self.is_admin: bool = data["isAdmin"]
        self.is_moderator: bool = data["isModerator"]
        self.is_rejoin: bool = data["isRejoin"]  # what does this mean

        self.clan: CharacterClan | None = _class_or_none(data["clan"], CharacterClan)  # pyright: ignore[reportAny]
        self.clan_role: CharacterClanRole | None = _class_or_none(
            data["clanRole"],  # pyright: ignore[reportAny]
            CharacterClanRole,
        )
        self.attack: CharacterStat = CharacterStat(
            Skills.Attack,
            data["skills"]["attack"],  # pyright: ignore[reportAny]
            data["skills"]["attackLevel"],  # pyright: ignore[reportAny]
        )
        self.defense: CharacterStat = CharacterStat(
            Skills.Defense,
            data["skills"]["defense"],  # pyright: ignore[reportAny]
            data["skills"]["defenseLevel"],  # pyright: ignore[reportAny]
        )
        self.strength: CharacterStat = CharacterStat(
            Skills.Strength,
            data["skills"]["strength"],  # pyright: ignore[reportAny]
            data["skills"]["strengthLevel"],  # pyright: ignore[reportAny]
        )
        self.health: CharacterStat = CharacterStat(
            Skills.Health,
            data["skills"]["health"],  # pyright: ignore[reportAny]
            data["skills"]["healthLevel"],  # pyright: ignore[reportAny]
        )
        self.magic: CharacterStat = CharacterStat(
            Skills.Magic,
            data["skills"]["magic"],  # pyright: ignore[reportAny]
            data["skills"]["magicLevel"],  # pyright: ignore[reportAny]
        )
        self.ranged: CharacterStat = CharacterStat(
            Skills.Ranged,
            data["skills"]["ranged"],  # pyright: ignore[reportAny]
            data["skills"]["rangedLevel"],  # pyright: ignore[reportAny]
        )
        self.woodcutting: CharacterStat = CharacterStat(
            Skills.Woodcutting,
            data["skills"]["woodcutting"],  # pyright: ignore[reportAny]
            data["skills"]["woodcuttingLevel"],  # pyright: ignore[reportAny]
        )
        self.fishing: CharacterStat = CharacterStat(
            Skills.Fishing,
            data["skills"]["fishing"],  # pyright: ignore[reportAny]
            data["skills"]["fishingLevel"],  # pyright: ignore[reportAny]
        )
        self.mining: CharacterStat = CharacterStat(
            Skills.Mining,
            data["skills"]["mining"],  # pyright: ignore[reportAny]
            data["skills"]["miningLevel"],  # pyright: ignore[reportAny]
        )
        self.crafting: CharacterStat = CharacterStat(
            Skills.Crafting,
            data["skills"]["crafting"],  # pyright: ignore[reportAny]
            data["skills"]["craftingLevel"],  # pyright: ignore[reportAny]
        )
        self.cooking: CharacterStat = CharacterStat(
            Skills.Cooking,
            data["skills"]["cooking"],  # pyright: ignore[reportAny]
            data["skills"]["cookingLevel"],  # pyright: ignore[reportAny]
        )
        self.farming: CharacterStat = CharacterStat(
            Skills.Farming,
            data["skills"]["farming"],  # pyright: ignore[reportAny]
            data["skills"]["farmingLevel"],  # pyright: ignore[reportAny]
        )
        self.slayer: CharacterStat = CharacterStat(
            Skills.Slayer,
            data["skills"]["slayer"],  # pyright: ignore[reportAny]
            data["skills"]["slayerLevel"],  # pyright: ignore[reportAny]
        )
        self.sailing: CharacterStat = CharacterStat(
            Skills.Sailing,
            data["skills"]["sailing"],  # pyright: ignore[reportAny]
            data["skills"]["sailingLevel"],  # pyright: ignore[reportAny]
        )
        self.healing: CharacterStat = CharacterStat(
            Skills.Healing,
            data["skills"]["healing"],  # pyright: ignore[reportAny]
            data["skills"]["healingLevel"],  # pyright: ignore[reportAny]
        )
        self.gathering: CharacterStat = CharacterStat(
            Skills.Gathering,
            data["skills"]["gathering"],  # pyright: ignore[reportAny]
            data["skills"]["gatheringLevel"],  # pyright: ignore[reportAny]
        )
        self.alchemy: CharacterStat = CharacterStat(
            Skills.Alchemy,
            data["skills"]["alchemy"],  # pyright: ignore[reportAny]
            data["skills"]["alchemyLevel"],  # pyright: ignore[reportAny]
        )
        self.combat_level: int = int(
            (
                (
                    self.attack.level
                    + self.defense.level
                    + self.health.level
                    + self.strength.level
                )
                / 4
            )
            + ((self.ranged.level + self.magic.level + self.healing.level) / 8)
        )
        self.stats: list[CharacterStat] = [
            self.attack,
            self.defense,
            self.strength,
            self.health,
            self.magic,
            self.ranged,
            self.woodcutting,
            self.fishing,
            self.mining,
            self.crafting,
            self.cooking,
            self.farming,
            self.slayer,
            self.sailing,
            self.healing,
            self.gathering,
            self.alchemy,
        ]
        self._skill_dict: dict[Skills, CharacterStat] = {
            Skills.Attack: self.attack,
            Skills.Defense: self.defense,
            Skills.Strength: self.strength,
            Skills.Health: self.health,
            Skills.Woodcutting: self.woodcutting,
            Skills.Fishing: self.fishing,
            Skills.Mining: self.mining,
            Skills.Crafting: self.crafting,
            Skills.Cooking: self.cooking,
            Skills.Farming: self.farming,
            Skills.Slayer: self.slayer,
            Skills.Magic: self.magic,
            Skills.Ranged: self.ranged,
            Skills.Sailing: self.sailing,
            Skills.Healing: self.healing,
            Skills.Gathering: self.gathering,
            Skills.Alchemy: self.alchemy,
        }

        state: dict[str, Any] = data["state"]  # pyright: ignore [reportAny, reportExplicitAny]
        self.hp: int = state["health"]
        self.in_raid: bool = state["inRaid"]
        self.in_arena: bool = state["inArena"]
        self.in_dungeon: bool = state["inDungeon"]
        self.in_onsen: bool = state["inOnsen"]
        self.is_resting: bool = self.in_onsen
        self.has_joined_dungeon: bool = state["joinedDungeon"]
        self.exp_per_hour: int = state["expPerHour"]
        if not self.exp_per_hour:
            self.exp_per_hour = 0

        self.training: Skills | None = None
        self.island: Islands = Islands.Sailing
        if state["island"] != "None":
            self.island = (
                _getenum_or_none(cast("str", state["island"]), Islands) or Islands.Unknown
            )
        self.destination: Islands = (
            _getenum_or_none(cast("str", state["destination"]), Islands)
            or Islands.Unknown
        )
        self.waiting_for_ferry: bool = False
        self.estimated_level_time: datetime = _call_or_none(
            cast("str", state["estimatedTimeForLevelUp"]), _parse_time
        ) or datetime.min.replace(tzinfo=UTC)
        self.x: int = state["x"]
        self.y: int = state["y"]
        self.z: int = state["z"]
        self.rested_time: timedelta = timedelta(
            seconds=int(cast("str", state["restedTime"]))
        )
        self.is_captain: bool = state["isCaptain"]

        _big_int_max = 2147483647

        self.auto_join_dungeon_count: int = state["autoJoinDungeonCounter"]
        if state["autoJoinDungeonCounter"] == _big_int_max:
            self.auto_join_dungeon_count = cast("int", math.inf)
        self.auto_join_raid_count: int = state["autoJoinRaidCounter"]
        if state["autoJoinRaidCounter"] == _big_int_max:
            self.auto_join_raid_count = cast("int", math.inf)
        self.is_auto_resting: bool = state["isAutoResting"]
        self.auto_rest_start: int = state["autoRestStart"]
        self.auto_rest_target: int = state["autoRestTarget"]

        self.dungeon_combat_style: Skills | None = _call_or_none(
            cast("str", state["dungeonCombatStyle"]), Skills
        )
        self.raid_combat_style: Skills | None = _call_or_none(
            cast("str", state["raidCombatStyle"]), Skills
        )

        self.items: list[CharacterItem] = []
        self._equipment: list[CharacterItem] = []
        self._id_item: dict[str, list[CharacterItem]] = {}
        invitems: list[dict[str, Any]] = cast(  # pyright: ignore [reportExplicitAny]
            "list[dict[str, Any]]",  # pyright: ignore [reportExplicitAny]
            data["inventoryItems"],
        )
        for invitem in invitems:
            char_item = CharacterItem(**invitem)
            if char_item.equipped:
                self._equipment.append(char_item)
            else:
                self.items.append(char_item)
            if char_item.item.id not in self._id_item:
                self._id_item[char_item.item.id] = []
            self._id_item[char_item.item.id].append(char_item)
        self.equipment: CharacterEquipment = CharacterEquipment(self._equipment)

        for item in self.equipment:
            item: CharacterItem
            if not item.active:
                continue
            for enchant in item.enchantments:
                if enchant.stat not in {
                    Enchantments.Power,
                    Enchantments.Aim,
                    Enchantments.Armor,
                }:
                    self.get_skill(Skills(enchant.stat.value))._add_enchant(
                        enchant.percentage
                    )

        self.status_effects: list[CharacterStatusEffect] = []
        effects: list[dict[str, Any]] = cast(  # pyright: ignore [reportExplicitAny]
            "list[dict[str, Any]]",  # pyright: ignore [reportExplicitAny]
            data["statusEffects"],
        )
        for effect in effects:
            self.status_effects.append(CharacterStatusEffect(**effect))

        _min_match_score = 90

        self.target_item: CharacterItem | None = None
        if state["task"] == "Fighting":
            task_arg: str = cast("str", state["taskArgument"]).capitalize()
            replace = fighting_replacements.get(task_arg)
            if replace:
                task_arg = replace
            self.training = Skills[task_arg]
        elif (not state["task"]) or cast("str", state["task"]).lower() == "none":
            pass
        else:
            self.training = Skills[(cast("str", state["task"])).capitalize()]
            fuzz_result: list[FuzzResult] = cast(
                "list[FuzzResult]",
                thefuzz.process.extract(  # pyright: ignore[reportUnknownMemberType]
                    cast("str", state["taskArgument"]),
                    _items_names,
                    limit=1,
                    scorer=thefuzz.fuzz.ratio,  # pyright: ignore[reportUnknownMemberType]
                ),
            )
            target_item_name, f_score = fuzz_result[0]
            if f_score > _min_match_score:
                target_item = _items_name_data[target_item_name]
                inv_item = self.get_item(target_item)
                if not inv_item:
                    inv_item = CharacterItem(
                        itemId=target_item.id,
                        amount=0,
                        equipped=False,
                        soulbound=False,
                        enchantment="",
                    )
                self.target_item = inv_item
        if self.training == Skills.Melee:
            self.training = Skills.All

        if not self.training and not self.island:
            self.training = Skills.Sailing

        self.training_stats: list[CharacterStat] = []
        if self.training:
            if self.training in (Skills.All, Skills.Health, Skills.Melee):
                self.training_stats.extend(
                    [self.health, self.attack, self.defense, self.strength]
                )
            else:
                self.training_stats.append(self.get_skill(self.training))
                if self.training in combat_skills:
                    self.training_stats.append(self.health)

            if self.in_raid or self.in_dungeon:
                self.training_stats.append(self.slayer)

        self.training_skills: list[Skills] = []
        for char_stat in self.training_stats:
            self.training_skills.append(char_stat.skill)

    def get_item(self, item: Item | str | itemdefs.Items):
        """Get the first instance of an item in the character's inventory or equipment."""
        result = self.get_all_item(item)
        if not result:
            return None
        return result[0]

    def get_all_item(self, item: Item | str | itemdefs.Items) -> list[CharacterItem]:
        """Get all instances of an item in the character's inventory or equipment."""
        query = None
        _number_of_dashes_in_a_uuid = 4
        if isinstance(item, Item):
            query = item.id
        elif item.count("-") == _number_of_dashes_in_a_uuid:
            query = item
        else:
            item_query = get_item(item)
            if item_query:
                query = item_query.id

        if query:
            return self._id_item.get(query, [])
        return []

    def get_skill(self, skill: Skills):
        """Get a character stat by skill."""
        return self._skill_dict[skill]

    @override
    def __repr__(self):
        return f"Character({self.name}, {self.id}, char_index={self.character_index})"


class ExpMult:
    """Experience multiplier event."""

    def __init__(self, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self.start_time: datetime = _parse_time(
            cast("str", kwargs.get("startTime", _ISO_EPOCH))
        )
        self.end_time: datetime = _parse_time(
            cast("str", kwargs.get("endTime", _ISO_EPOCH))
        )
        self.multiplier: float = kwargs.get("multiplier", 1.0)
        self.event_name: str = kwargs.get("eventName", "")

    @override
    def __repr__(self):
        return (
            f"ExpMult({self.multiplier}x from "
            f"{self.start_time.isoformat()} to {self.end_time.isoformat()})"
        )


class MarketplaceItem:
    """Item listed on the marketplace."""

    def __init__(self, rfapi: RavenNest, **kwargs: Any):  # pyright: ignore [reportExplicitAny, reportAny]
        self._rf_api: RavenNest = rfapi
        self.seller_char_id: str = kwargs.get("sellerCharacterId", "")
        self._seller_user_id: str = kwargs.get("sellerUserId", "")
        self.item: Item = _items_id_data[kwargs.get("itemId", "")]
        self.amount: int = kwargs.get("amount", 0)
        self.price_per_item: int = kwargs.get("pricePerItem", 0)
        self.expires: datetime = _parse_time(
            cast("str", kwargs.get("expires", _ISO_EPOCH))
        )
        self.created: datetime = _parse_time(
            cast("str", kwargs.get("created", _ISO_EPOCH))
        )
        self.enchantment: ItemEnchantment | None = _class_or_none(
            kwargs.get("enchantment"), ItemEnchantment
        )

    async def get_seller(self) -> Character:
        """Get the character that is selling this item."""
        result = await self._rf_api._get_character(self.seller_char_id)
        return Character(cast("dict[str, Any]", result))  # pyright: ignore [reportExplicitAny]

    @override
    def __repr__(self):
        return (
            f"MarketplaceItem({self.item.name}, x{self.amount}, "
            f"{self.price_per_item} coins each, listed by {self.seller_char_id})"
        )


class UnexpectedStatusCodeError(Exception):
    """Raised when the RavenNest API returns an unexpected status code."""


class RavenNest:
    """RavenNest API client."""

    def __init__(self, username: str, password: str):
        self._user: str = username
        self._pass: str = password
        self._auth: str = ""
        self._baseURL: str = "https://www.ravenfall.stream/api"
        self.is_authing: asyncio.Future[Any] | None = None  # pyright: ignore [reportExplicitAny]

    async def login(self):
        """Authenticate with the RavenNest API and load all item data."""
        _ = await self._authenticate()
        if self._auth:
            await self.refresh_items()

    async def refresh_items(self):
        """Refresh the item data from the API.

        Call this if you want to make sure you have the latest item data,
        but it is not necessary to call this after login()
        as login() already calls this.
        """
        item_data = await _fetch_raw_item_data(self)
        _load_item_data(item_data)

    async def _authenticate(self):
        if self.is_authing is None or self.is_authing.done():
            self.is_authing = asyncio.get_running_loop().create_future()
        elif not self.is_authing.done():
            result: Any = await self.is_authing  # pyright: ignore [reportAny, reportExplicitAny]
            if result:
                return None
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                self._baseURL + "/auth",
                json={"username": self._user, "password": self._pass},
                ssl=False,
            )
            response = await r.text()
        if '"token"' in response:
            self._auth = str(base64.b64encode(bytes(response, "utf-8")), "utf-8")
            LOGGER.info("RavenNest: Auth successful")
            self.is_authing.set_result(True)
            return True
        LOGGER.error("RavenNest: Auth unsuccessful!")
        self.is_authing.set_result(False)
        return False

    async def _get(
        self, path: str, *, reauth: bool = True
    ) -> dict[str, int | float | bool | str] | list[Any]:  # pyright: ignore [reportExplicitAny]
        if not self._auth:
            LOGGER.warning("RavenNest: Not authenticated! Call login() first!")
            return {}
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                self._baseURL + path,
                headers={"auth-token": self._auth, "Accept": "application/json"},
                ssl=False,
            )
            if r.status == 204:  # noqa: PLR2004
                return {}
            if r.status != 200:  # noqa: PLR2004
                if reauth:
                    _ = await self._authenticate()
                    _ = await self._get(path, reauth=False)
                else:
                    LOGGER.error(f"RavenNest: (got unexpected status {r.status})")
                    msg = f"RavenNest API returned unexpected status code {r.status}"
                    raise UnexpectedStatusCodeError(msg)
            return cast("dict[str, int | float | bool | str]", await r.json())

    async def _items(self) -> list[RFItemJson]:
        return cast("list[RFItemJson]", await self._get("/Items"))

    async def _drops(self) -> list[RFItemDropJson]:
        return cast("list[RFItemDropJson]", await self._get("/Items/drops"))

    async def _redeemables(self) -> list[RFItemRedeemableJson]:
        return cast("list[RFItemRedeemableJson]", await self._get("/Items/redeemable"))

    async def _recipes(self) -> list[RFRecipeJson]:
        return cast("list[RFRecipeJson]", await self._get("/Items/recipes"))

    async def _exp_multiplier(self):
        return await self._get("/Game/exp-multiplier")

    async def _get_players_twitch(self, twitch_id: str, char_id: int | str = 1):
        return await self._get(f"/Players/twitch/{twitch_id}/{char_id}")

    async def _get_character(self, character_id: str):
        return await self._get(f"/Players/{character_id}")

    @alru_cache(ttl=29)
    async def _get_marketplace(self, offset: int = 0, size: int = 99999, *_):
        return cast(
            "list[dict[str, Any]]",  # pyright: ignore [reportExplicitAny]
            await self._get(f"/Marketplace/{offset}/{size}"),
        )

    @alru_cache(ttl=4)
    async def get_character(self, twitch_uid: str, character_id: int | str = 1, *_):
        """Get a character by Twitch UID and character index (1-based)."""
        result = await self._get_players_twitch(twitch_uid, character_id)
        if not result:
            return None
        return Character(cast("dict[str, Any]", result))  # pyright: ignore [reportExplicitAny]

    @alru_cache(ttl=4)
    async def get_character_from_id(self, ravenfall_char_id: str, *_):
        """Get a character by Ravenfall character ID."""
        result = await self._get_character(ravenfall_char_id)
        if not result:
            return None
        return Character(cast("dict[str, Any]", result))  # pyright: ignore [reportExplicitAny]

    @alru_cache(ttl=3)
    async def get_global_mult(self, *_):
        """Get the current global experience multiplier event, if there is one."""
        result = await self._exp_multiplier()
        return ExpMult(**cast("dict[str, Any]", result))  # pyright: ignore [reportExplicitAny]

    @alru_cache(ttl=30)
    async def get_marketplace(self, *_) -> tuple[MarketplaceItem, ...]:
        """Get all items currently listed on the marketplace."""
        result = await self._get_marketplace()
        market_items = [MarketplaceItem(rfapi=self, **x) for x in result]
        market_items.sort(key=lambda x: x.created, reverse=True)
        return tuple(market_items)


MAX_LEVEL = 999
experience_array = [0] * MAX_LEVEL

exp_for_level = 100
for level_index in range(MAX_LEVEL):
    level = level_index + 1
    tenth = math.trunc(level / 10) + 1
    incrementor = tenth * 100 + math.pow(tenth, 3)
    exp_for_level += math.trunc(incrementor)
    experience_array[level_index] = exp_for_level

_dirname = Path(__file__).parent

_items = []
_items_name_data: dict[str, Item] = {}
_items_id_data: dict[str, Item] = {}
_items_names: list[str] = []
_items_list: list[Item] = []


def _load_item_data(item_list: list[InternalItemData]):
    global _items  # noqa: PLW0603

    _items_names.clear()
    _items_list.clear()

    _items = item_list
    for item in _items:
        item_thing = Item(item)
        _items_name_data[item_thing.name] = item_thing
        _items_id_data[item_thing.id] = item_thing
        _items_names.append(item_thing.name)
        _items_list.append(item_thing)
    for item in _items_id_data.values():
        if item._craft_fail_item:
            item.craft_fail_item = _items_id_data[item._craft_fail_item]
        for ing in item._craft_ingredients:
            item.craft_ingredients.append(
                Ingredient(item=_items_id_data[ing["item_id"]], amount=ing["amount"])
            )
        for uitem in item._used_in:
            item.used_in.append(_items_id_data[uitem])


def load_local_item_data():
    """Load item data from the local JSON file.

    This is used to have item data available without
    needing to authenticate with the API, but it may be outdated.
    Call refresh_items() after login()
    to get the latest item data from the API.
    """
    with Path(_dirname, "data/items.json").open("r") as f:
        _a: list[InternalItemData] = cast("list[InternalItemData]", json.load(f))
        _load_item_data(_a)


equipment_levels = {
    ItemMaterials.Iron: 1,
    ItemMaterials.Bronze: 1,
    ItemMaterials.Steel: 10,
    ItemMaterials.Black: 20,
    ItemMaterials.Mithril: 30,
    ItemMaterials.Adamantite: 50,
    ItemMaterials.Rune: 70,
    ItemMaterials.Dragon: 90,
    ItemMaterials.Abraxas: 120,
    ItemMaterials.Phantom: 150,
    ItemMaterials.Lionsbane: 200,
    ItemMaterials.Ether: 280,
    ItemMaterials.Ancient: 340,
    ItemMaterials.Atlarus: 400,
    ItemMaterials.ElderBronze: 500,
    ItemMaterials.ElderIron: 525,
    ItemMaterials.ElderSteel: 550,
    # ItemMaterials.ElderBlack: 600,
    ItemMaterials.ElderMithril: 650,
    ItemMaterials.ElderAdamantite: 700,
    ItemMaterials.ElderRune: 750,
    ItemMaterials.ElderDragon: 800,
    ItemMaterials.ElderAbraxas: 825,
    ItemMaterials.ElderPhantom: 850,
    ItemMaterials.ElderLionsbane: 875,
    ItemMaterials.ElderEther: 900,
    ItemMaterials.ElderAncient: 950,
    ItemMaterials.ElderAtlarus: 999,
}

island_ranges = {
    (1, 99): Islands.Home,
    (50, 150): Islands.Away,
    (100, 300): Islands.Ironhill,
    (200, 400): Islands.Kyo,
    (300, 700): Islands.Heim,
    (500, 900): Islands.Atria,
    (700, math.inf): Islands.Eldara,
}


def experience_for_level(level: int) -> int:
    """Get the total experience required to reach a certain level."""
    if level - 2 >= len(experience_array):
        return experience_array[len(experience_array) - 1]
    return 0 if level - 2 < 0 else experience_array[level - 2]


def search_item(name: str, limit: int = 10) -> list[tuple[Item, int]]:
    """Search for items by name."""
    search_result: list[FuzzResult] = cast(
        "list[FuzzResult]",
        thefuzz.process.extract(  # pyright: ignore[reportUnknownMemberType]
            name,
            _items_names,
            limit=limit,
            scorer=thefuzz.fuzz.ratio,  # pyright: ignore[reportUnknownMemberType]
        ),
    )
    out_results: list[tuple[Item, int]] = []
    for result, score in search_result:
        out_results.append((_items_name_data[result], score))
    return out_results


def get_item(item: str | Item | itemdefs.Items):
    """Get an item by name, ID, or Item instance."""
    if isinstance(item, Item):
        return item
    _number_of_dashes_in_a_uuid = 4
    if item.count("-") == _number_of_dashes_in_a_uuid:
        return _items_id_data.get(item)
    return _items_name_data.get(item)


def get_all_items():
    """Get a list of all items."""
    return _items_list


def get_all_item_names():
    """Get a list of all item names."""
    return _items_names


def get_raw_item_data():
    """Get the raw item data."""
    return _items


def get_island_for_level(level: int):
    """Get the island associated with a certain level."""
    for (min_lvl, max_lvl), island in reversed(island_ranges.items()):
        if min_lvl <= level <= max_lvl:
            return island
    return Islands.Unknown


def get_material_for_level(level: int):
    """Get the material associated with a certain level."""
    for material, m_level in reversed(equipment_levels.items()):
        if m_level <= level:
            return material
    return ItemMaterials.ElderAtlarus


load_local_item_data()

fighting_skills = (
    Skills.Attack,
    Skills.Defense,
    Skills.Strength,
    Skills.Health,
    Skills.Magic,
    Skills.Ranged,
    Skills.Healing,
    Skills.All,
    Skills.Melee,
)
combat_skills = fighting_skills
resource_skills = (
    Skills.Mining,
    Skills.Gathering,
    Skills.Woodcutting,
    Skills.Farming,
    Skills.Fishing,
)
