import asyncio
import aiohttp
from async_lru import alru_cache
import base64
import json
from enum import Enum
from typing import List, Dict, Any, Callable, Tuple
import math
from datetime import datetime, timedelta, timezone
import os
import time

import thefuzz
import thefuzz.fuzz
import thefuzz.process
from . import itemdefs
from .enums import *
from .itemdata import _fetch_raw_item_data


class ItemEffect:
    def __init__(self, **kwargs):
        self.effect: Effects = kwargs.get('effect')
        self.duration: float = kwargs.get('duration')
        self.percentage: float = kwargs.get('percentage')
        self.min_amount: float = kwargs.get('min_amount')
        
    def __repr__(self):
        return f"ItemEffect({self.effect.name}, {self.duration}s, {self.percentage}, {self.min_amount})"

class ItemRequirement:
    def __init__(self, **kwargs):
        self.skill: Skills = kwargs.get('skill')
        self.level: int = kwargs.get('level')
    
    def __repr__(self):
        return f"ItemRequirement({self.skill.name}, {self.level})"

class ItemStat:
    def __init__(self, **kwargs):
        self.stat: Stat = kwargs.get('stat')
        self.level: int = kwargs.get('level')
    
    def __repr__(self):
        return f"ItemStat({self.stat.name}, {self.level})"

class Ingredient:
    def __init__(self, **kwargs):
        self.item: Item = kwargs.get('item')
        self.amount: int = kwargs.get('amount')
        
    def __repr__(self):
        return f"Ingredient({self.item.name}, {self.amount})"

class Item:
    def __init__(self, data: dict):
        self.id: str = data.get('id')
        self.name: str = data.get('name')
        self.description: str = data.get('description')
        self.level: int = data.get('level')
        self.type: ItemTypes | None = ItemTypes(data.get("type")) if data.get('type') else None
        self.category: ItemCategory | None = ItemCategory(data.get("category")) if data.get('category') is not None else None
        self.material: ItemMaterials | None = ItemMaterials(data.get("material")) if data.get('material') else None
        self.sell_price: int = data.get("sell_price")
        self.buy_price: int = data.get("buy_price")
        self.enchantments: int = data.get("enchantments")
        self.soulbound: bool = data.get("soulbound")
        self.craft_skill: Skills | None = Skills[data.get("craft_skill")] if data.get("craft_skill") else None
        self.craft_level: int = data.get("craft_level")
        self.min_success_rate: float = data.get("min_success_rate")
        self.max_success_rate: float = data.get("max_success_rate")
        self.preparation_time: int = data.get("preperation_time")
        self.is_fixed_success_rate = data.get("is_fixed_success_rate")
        self.drop_skill: Skills | None = Skills[data.get("drop_skill")] if data.get("drop_skill") else None
        self.drop_level: int = data.get("drop_level")
        self.drop_chance: float = data.get("drop_chance")
        self.drop_cooldown: int = data.get("drop_cooldown")
        self.raid_drop_month_start: int = data.get("raid_drop_month_start")
        self.raid_drop_month_length: int = data.get("raid_drop_month_length")
        self.raid_min_drop: int = data.get("raid_min_drop")
        self.raid_max_drop: int = data.get("raid_max_drop")
        self.raid_drop_tier: int = data.get("raid_drop_tier")
        self.drop_slayer_requirement: int = data.get("drop_slayer_requirement")

        self._craft_fail_item = data.get("craft_fail_item")
        self._craft_ingredients = data.get("craft_ingredients")
        self._used_in = data.get("used_in")
        self._modified = data.get("modified")

        self.craft_fail_item: Item = None
        self.craft_ingredients: List[Ingredient] = []
        self.used_in: List[Item] = []

        self.effects: List[ItemEffect] = []
        for effect in data.get("effects"):
            self.effects.append(ItemEffect(**{
                "effect": Effects(effect['id']),
                "duration": effect["duration"],
                "percentage": effect["percentage"],
                "min_amount": effect["min_amount"],
            }))

        self.equip_requirements: List[ItemRequirement] = []
        for req in data.get('equip_requirements'):
            self.equip_requirements.append(ItemRequirement(**{
                "skill": Skills(req['skill']),
                "level": req['level']
            }))

        self.stats: List[ItemStat] = []
        for stat in data.get('stats'):
            self.stats.append(ItemStat(**{
                "stat": Stat(stat['stat']),
                "level": stat['level']
            }))
    
    def __eq__(self, value: 'Item'):
        return self.id == value.id
    
    def __repr__(self):
        return f"Item({self.name}, {self.id})"

class CharacterStat:
    def __init__(self, skill: Skills, exp: float, level: int):
        self.skill: Skills = skill
        self.level: int = level
        self.level_exp: int = exp
        self.total_exp_for_level: float = experience_for_level(level+1)
        self.enchant_percent: float = 0
        self.enchant_levels: int = 0

    def _add_enchant(self, percent):
        self.enchant_percent += percent
        self.enchant_levels = round(self.level * self.enchant_percent)
    
    def __repr__(self):
        return f"CharacterStat({self.skill.name}, {self.level}, {self.level_exp}xp, {self.enchant_percent}%, {self.enchant_levels})"

class ClanStat:
    def __init__(self, **kwargs):
        self.skill: ClanSkill = ClanSkill[kwargs.get('name').capitalize()]
        self.level: int = kwargs.get('level')
        self.experience: int = kwargs.get('experience')
        self.max_level: int = kwargs.get('maxLevel')
    
    def __repr__(self):
        return f"ClanStat({self.skill.name}, {self.level}, {self.experience}xp)"

class CharacterClanRole:
    def __init__(self, **kwargs):
        self.role: ClanRole = ClanRole(kwargs.get('level'))
        self.joined: datetime = _parse_time(kwargs.get('joined'))
    
    def __repr__(self):
        return f"CharacterClanRole({self.role.name}, joined {self.joined.isoformat()})"

class CharacterClan:
    def __init__(self, **kwargs):
        self.id: str = kwargs.get('id')
        self.owner_twitch_id: str = kwargs.get('owner')
        self.owner_id: str = kwargs.get('ownerUserId')
        self.level: int = kwargs.get('level')
        self.experience: int = kwargs.get('experience')
        self.name: str = kwargs.get('name')
        self.logo: str = kwargs.get('logo')
        self.skills: List[ClanStat] = []
        skills = kwargs.get('clanSkills')
        if skills:
            for skill in skills:
                self.skills.append(ClanStat(**skill))
    
    def __repr__(self):
        return f"CharacterClan({self.name}, level {self.level}, {self.experience}xp)"

class ItemEnchantment:
    def __init__(self, enchant_string: str):
        stat, percentage = enchant_string.split(":")
        self.percentage: float = float(percentage.rstrip('%'))/100
        self.stat: Enchantments = Enchantments[stat.capitalize()]
    
    def __repr__(self):
        return f"ItemEnchantment({self.stat.name}, {self.percentage*100}%)"

class CharacterItem:
    def __init__(self, **kwargs):
        self.item: Item = _items_id_data[kwargs.get('itemId')]
        self.amount: int = kwargs.get('amount')
        self.equipped: bool = kwargs.get('equipped')
        self.soulbound: bool = kwargs.get('soulbound')
        enchantment: str = kwargs.get('enchantment')
        self.enchantments: List[ItemEnchantment] = []
        self.active: bool = False
        if enchantment:
            for item in enchantment.split(";"):
                self.enchantments.append(ItemEnchantment(item))

    def _set_active(self, value):
        self.active = value
        
    def __repr__(self):
        return f"CharacterItem({self.item.name}, x{self.amount}, equipped={self.equipped}, soulbound={self.soulbound}, enchantments={self.enchantments}, active={self.active})"

class CharacterStatusEffect:
    def __init__(self, **kwargs):
        self.effect = Effects(kwargs.get('type'))
        self.amount: float = kwargs.get('amount')
        self.duration: float = kwargs.get('duration')
        self.time_left: float = kwargs.get('timeLeft')
        self.start_time: datetime = _parse_time(kwargs.get('startUtc'))
        self.expires: datetime = _parse_time(kwargs.get('expiresUtc'))
        
    def __repr__(self):
        return f"CharacterStatusEffect({self.effect.name}, {self.amount}, {self.duration}s, {self.time_left}s left)"

class CharacterEquipment:
    def __init__(self, equipment: List[CharacterItem]):
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
            item._set_active(True)
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

        if self.weapon and self.shield:
            if self.weapon.item.type in [
                ItemTypes.TwoHandedSword,
                ItemTypes.TwoHandedAxe,
                ItemTypes.TwoHandedSpear
            ]:
                self.shield._set_active(False)

    def __iter__(self) -> List[CharacterItem]:
        out = [
            self.helmet, self.chest, self.gloves, self.leggings, self.boots, 
            self.ring, self.amulet, self.staff, self.weapon, self.bow, self.pet, 
            self.shield,
        ]
        return [x for x in out if x].__iter__()

    def __repr__(self):
        return f"CharacterEquipment"

fighting_replacements = {
    "Atk": "Attack",
    "Att": "Attack",
    "Heal": "Healing",
    "Def": "Defense",
    "Str": "Strength"
}

def _parse_time(iso_str: str):
    s = ""
    if iso_str[-1] == "Z":
        s = iso_str[:-1] + '+00:00'
    else:
        s = iso_str + '+00:00'
    return datetime.fromisoformat(s)

def _class_or_none(_obj: Any, _class: Any):
    if _obj is not None:
        return _class(**_obj)

def _getitem_or_none(_obj: Any, _obj2: Any):
    if _obj is not None:
        return _obj2[_obj]

def _call_or_none(_obj: Any, _callable: Callable):
    if _obj is not None:
        return _callable(_obj)

class Character:
    def __init__(self, data):
        self._raw: Dict = data
        self.time_recieved = datetime.now(timezone.utc)
        self.id: str = data['id']
        self.char_id: str = self.id
        self.user_id: str = data['userId']
        self.user_name: str = data['userName']
        self.twitch_id: str = data['twitch']['platformId']
        self.identifier: str = data['identifier']
        self.character_index: int = data['characterIndex']+1
        self.index = self.character_index
        if not self.identifier:
            self.identifier = str(self.character_index)
        self.name: str = self.identifier
        self.patreon_tier: int = data['patreonTier']
        self.is_hidden_in_highscore: bool = data['isHiddenInHighscore']
        self.coins: int = data['resources']['coins']

        self.is_admin: bool = data['isAdmin']
        self.is_moderator: bool = data['isModerator']
        self.is_rejoin: bool = data['isRejoin']  # what does this mean

        self.clan: CharacterClan | None = _class_or_none(data['clan'], CharacterClan)
        self.clan_role: CharacterClanRole | None = _class_or_none(data['clanRole'], CharacterClanRole)
        self.attack = CharacterStat(Skills.Attack, data['skills']['attack'], data['skills']['attackLevel'])
        self.defense = CharacterStat(Skills.Defense, data['skills']['defense'], data['skills']['defenseLevel'])
        self.strength = CharacterStat(Skills.Strength, data['skills']['strength'], data['skills']['strengthLevel'])
        self.health = CharacterStat(Skills.Health, data['skills']['health'], data['skills']['healthLevel'])
        self.magic = CharacterStat(Skills.Magic, data['skills']['magic'], data['skills']['magicLevel'])
        self.ranged = CharacterStat(Skills.Ranged, data['skills']['ranged'], data['skills']['rangedLevel'])
        self.woodcutting = CharacterStat(Skills.Woodcutting, data['skills']['woodcutting'], data['skills']['woodcuttingLevel'])
        self.fishing = CharacterStat(Skills.Fishing, data['skills']['fishing'], data['skills']['fishingLevel'])
        self.mining = CharacterStat(Skills.Mining, data['skills']['mining'], data['skills']['miningLevel'])
        self.crafting = CharacterStat(Skills.Crafting, data['skills']['crafting'], data['skills']['craftingLevel'])
        self.cooking = CharacterStat(Skills.Cooking, data['skills']['cooking'], data['skills']['cookingLevel'])
        self.farming = CharacterStat(Skills.Farming, data['skills']['farming'], data['skills']['farmingLevel'])
        self.slayer = CharacterStat(Skills.Slayer, data['skills']['slayer'], data['skills']['slayerLevel'])
        self.sailing = CharacterStat(Skills.Sailing, data['skills']['sailing'], data['skills']['sailingLevel'])
        self.healing = CharacterStat(Skills.Healing, data['skills']['healing'], data['skills']['healingLevel'])
        self.gathering = CharacterStat(Skills.Gathering, data['skills']['gathering'], data['skills']['gatheringLevel'])
        self.alchemy = CharacterStat(Skills.Alchemy, data['skills']['alchemy'], data['skills']['alchemyLevel'])
        self.combat_level = int(((self.attack.level + self.defense.level + self.health.level + self.strength.level) / 4) + ((self.ranged.level + self.magic.level + self.healing.level) / 8))
        #  (int)(((skills.AttackLevel + skills.DefenseLevel + skills.HealthLevel + skills.StrengthLevel) / 4f) + ((skills.RangedLevel + skills.MagicLevel + skills.HealingLevel) / 8f))
        self.stats = [
            self.attack, self.defense, self.strength, self.health, self.magic,
            self.ranged, self.woodcutting, self.fishing, self.mining, self.crafting,
            self.cooking, self.farming, self.slayer, self.sailing, self.healing,
            self.gathering, self.alchemy
        ]
        self._skill_dict = {
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
            Skills.Alchemy: self.alchemy
        }

        state = data['state']
        self.hp: int = state['health']
        self.in_raid: bool = state['inRaid']
        self.in_arena: bool = state['inArena']
        self.in_dungeon: bool = state['inDungeon']
        self.in_onsen: bool = state['inOnsen']
        self.is_resting: bool = self.in_onsen
        self.has_joined_dungeon: bool = state['joinedDungeon']
        self.exp_per_hour: int = state['expPerHour']
        if not self.exp_per_hour:
            self.exp_per_hour = 0

        self.training: Skills | None = None
        self.island: Islands = None
        if state['island'] != "None":
            self.island: Islands = _getitem_or_none(state['island'], Islands)
        self.destination: Islands = _getitem_or_none(state['destination'], Islands)
        # self.waiting_for_ferry: bool = self.destination and self.destination == self.island
        self.waiting_for_ferry: bool = False
        self.estimated_level_time: datetime = _call_or_none(state['estimatedTimeForLevelUp'], _parse_time)
        self.x: int = state['x']
        self.y: int = state['y']
        self.z: int = state['z']
        # self.rested_time_s = int(state['restedTime'])
        self.rested_time = timedelta(seconds=int(state['restedTime']))
        self.is_captain = state['isCaptain']

        self.auto_join_dungeon_count = state['autoJoinDungeonCounter']
        if state['autoJoinDungeonCounter'] == 2147483647:
            self.auto_join_dungeon_count = math.inf
        self.auto_join_raid_count = state['autoJoinRaidCounter']
        if state['autoJoinRaidCounter'] == 2147483647:
            self.auto_join_raid_count = math.inf
        self.is_auto_resting = state['isAutoResting']
        self.auto_rest_start = state['autoRestStart']
        self.auto_rest_target = state['autoRestTarget']
        
        self.dungeon_combat_style = _call_or_none(state['dungeonCombatStyle'], Skills)
        self.raid_combat_style = _call_or_none(state['raidCombatStyle'], Skills)

        self.items: List[CharacterItem] = []
        self._equipment: List[CharacterItem] = []
        self._id_item: Dict[str, List[CharacterItem]] = {}
        for item in data['inventoryItems']:
            char_item = CharacterItem(**item)
            if char_item.equipped:
                self._equipment.append(char_item)
            else:
                self.items.append(char_item)
            if not char_item.item.id in self._id_item:
                self._id_item[char_item.item.id] = []
            self._id_item[char_item.item.id].append(char_item)
        self.equipment = CharacterEquipment(self._equipment)

        for item in self.equipment:
            item: CharacterItem
            if not item.active:
                continue
            for enchant in item.enchantments:
                if enchant.stat.value < 17:  # not power, aim or armor
                    self.get_skill(Skills(enchant.stat.value))._add_enchant(enchant.percentage)
        
        self.status_effects: List[CharacterStatusEffect] = []
        for effect in data['statusEffects']:
            self.status_effects.append(CharacterStatusEffect(**effect))

        self.target_item: CharacterItem | None = None
        if state['task'] == "Fighting":
            task_arg = state['taskArgument'].capitalize()
            replace = fighting_replacements.get(task_arg)
            if replace:
                task_arg = replace
            self.training = Skills[task_arg]
        elif (not state['task']) or state['task'].lower() == "none":
            pass
        else:
            self.training = Skills[state['task'].capitalize()]
            target_item_name, f_score = thefuzz.process.extract(state['taskArgument'], _items_names, limit=1, scorer=thefuzz.fuzz.ratio)[0]
            if f_score > 90:
                target_item = _items_name_data[target_item_name]
                inv_item = self.get_item(target_item)
                if not inv_item:
                    inv_item = CharacterItem(
                        itemId=target_item.id,
                        amount=0,
                        equipped=False,
                        soulbound=False,
                        enchantment=''
                    )
                self.target_item = inv_item
        if self.training == Skills.Melee:
            self.training = Skills.All

        if not self.training:
            if (not self.island) or self.destination == Islands.Ferry:
                self.training = Skills.Sailing

        self.training_stats: List[CharacterStat] = []
        if self.training:
            if self.training in (Skills.All, Skills.Health, Skills.Melee):
                self.training_stats.extend([self.health, self.attack, self.defense, self.strength])
            else:
                self.training_stats.append(self.get_skill(self.training))
                if self.training in combat_skills:
                    self.training_stats.append(self.health)

            if self.in_raid or self.in_dungeon:
                self.training_stats.append(self.slayer)

        self.training_skills: List[Skills] = []
        for char_stat in self.training_stats:
            self.training_skills.append(char_stat.skill)

    def get_item(self, item: Item | str | itemdefs.Items):
        result = self.get_all_item(item)
        if not result:
            return None
        else:
            return result[0]

    def get_all_item(self, item: Item | str | itemdefs.Items):
        query = None
        if isinstance(item, Item):
            query = item.id
        elif isinstance(item, str):
            if item.count('-') == 4:
                query = item
            else:
                item_query = get_item(item)
                if item_query:
                    query = item_query.id
        elif isinstance(item, itemdefs.Items):
            query = item.value
        else:
            raise ValueError("bro...")
        return self._id_item.get(query)

    def get_skill(self, skill: Skills):
        return self._skill_dict[skill]
    
    def __repr__(self):
        return f"Character({self.name}, {self.id}, char_index={self.character_index})"


class ExpMult:
    def __init__(self, **kwargs):
        self.start_time = _parse_time(kwargs.get('startTime'))
        self.end_time = _parse_time(kwargs.get('endTime'))
        self.multiplier = kwargs.get('multiplier')
        self.event_name = kwargs.get("eventName")
        
    def __repr__(self):
        return f"ExpMult({self.multiplier}x from {self.start_time.isoformat()} to {self.end_time.isoformat()})"


class MarketplaceItem:
    def __init__(self, **kwargs):
        self._rf_api: RavenNest = kwargs.get('rfapi')
        self.seller_char_id: str = kwargs.get('sellerCharacterId')
        self._seller_user_id = kwargs.get('sellerUserId')
        self.item: Item = _items_id_data[kwargs.get('itemId')]
        self.amount: int = kwargs.get('amount')
        self.price_per_item: int = kwargs.get('pricePerItem')
        self.expires: datetime = _parse_time(kwargs.get('expires'))
        self.created: datetime = _parse_time(kwargs.get('created'))
        self.enchantment: ItemEnchantment | None = _class_or_none(kwargs.get('enchantment'), ItemEnchantment)
        
    async def get_seller(self):
        result = self._rf_api._get_character(self.seller_char_id)
        return Character(result)
    
    def __repr__(self):
        return f"MarketplaceItem({self.item.name}, x{self.amount}, {self.price_per_item} coins each, listed by {self.seller_char_id})"

class RavenNest:
    def __init__(self, username: str, password: str):
        self._user = username
        self._pass = password
        self._auth = ""
        self._baseURL = "https://www.ravenfall.stream/api"
        self.is_authing: asyncio.Future = None

    async def login(self):
        await self._authenticate()
        if self._auth:
            await self.refresh_items()
    
    async def refresh_items(self):
        item_data = await _fetch_raw_item_data(self)
        _load_item_data(item_data)

    async def _authenticate(self):
        if self.is_authing is None or self.is_authing.done():
            self.is_authing = asyncio.get_running_loop().create_future()
        elif not self.is_authing.done():
            result = await self.is_authing
            if result:
                return
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                self._baseURL + "/auth",
                json={
                    "username": self._user,
                    "password": self._pass
                },
                ssl=False
            )
            response = await r.text()
        if '"token"' in response:
            self._auth = str(base64.b64encode(bytes(response,"utf-8")),'utf-8')
            print("RavenNest: Auth successful")
            self.is_authing.set_result(True)
            return True
        else:
            print("RavenNest: Auth unsuccessful!")
            self.is_authing.set_result(False)
            return False

    async def _get(self, path, reauth=True):
        if not self._auth:
            print("RavenNest: Not authenticated! Call login() first!")
            return {}
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                self._baseURL + path,
                headers={
                    "auth-token": self._auth,
                    "Accept": "application/json"
                },
                ssl=False
            )
            if r.status == 204:
                return None
            elif r.status != 200:
                if reauth:
                    await self._authenticate()
                    await self._get(path, False)
                else:
                    print(f"WHAT (got {r.status})")
                    raise Exception("WHAT")
            return await r.json()

    async def _items(self):
        return await self._get(f"/Items")
    
    async def _drops(self):
        return await self._get(f"/Items/drops")
    
    async def _redeemables(self):
        return await self._get(f"/Items/redeemable")
    
    async def _recipes(self):
        return await self._get(f"/Items/recipes")

    async def _exp_multiplier(self):
        return await self._get(f"/Game/exp-multiplier")

    async def _get_players_twitch(self, twitch_id, char_id=1):
        return await self._get(f"/Players/twitch/{twitch_id}/{char_id}")
    
    async def _get_character(self, character_id):
        return await self._get(f"/Players/{character_id}")
    
    @alru_cache(ttl=29)
    async def _get_marketplace(self, offset=0, size=99999, *_):
        return await self._get(f"/Marketplace/{offset}/{size}")
    
    @alru_cache(ttl=4)
    async def get_character(self, twitch_uid, character_id=1, *_):
        result = await self._get_players_twitch(twitch_uid, character_id)
        if not result:
            return None
        return Character(result)
    
    @alru_cache(ttl=4)
    async def get_character_from_id(self, ravenfall_char_id, *_):
        result = await self._get_character(ravenfall_char_id)
        if not result:
            return None
        return Character(result)
    
    @alru_cache(ttl=3)
    async def get_global_mult(self, *_):
        result = await self._exp_multiplier()
        return ExpMult(**result)
    
    @alru_cache(ttl=30)
    async def get_marketplace(self, *_) -> Tuple[MarketplaceItem]:
        result = await self._get_marketplace()
        market_items = [MarketplaceItem(**x, rfapi=self) for x in result]
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

_dirname = os.path.dirname(__file__)

_items = []    
_items_name_data: Dict[str, Item] = {}
_items_id_data: Dict[str, Item] = {}
_items_names: List[str] = []
_items_list: List[Item] = []
def _load_item_data(item_list):
    global _items
    global _items_name_data
    global _items_id_data
    global _items_names

    _items_names.clear()
    _items_list.clear()

    _items = item_list
    for item in _items:
        item_thing = Item(item)
        _items_name_data[item_thing.name] = item_thing
        _items_id_data[item_thing.id] = item_thing
        _items_names.append(item_thing.name)
        _items_list.append(item_thing)
    for item_id, item in _items_id_data.items():
        if item._craft_fail_item:
            item.craft_fail_item = _items_id_data[item._craft_fail_item]
        for ing in item._craft_ingredients:
            item.craft_ingredients.append(Ingredient(**{
                'item': _items_id_data[ing['item_id']],
                'amount': ing['amount']
            }))
        for uitem in item._used_in:
            item.used_in.append(_items_id_data[uitem])

def load_local_item_data():
    with open(os.path.join(_dirname, 'data/items.json'), 'r') as f:
        _a = json.load(f)
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
    ItemMaterials.ElderAtlarus: 999
}

island_ranges = {
    (1, 99): Islands.Home,
    (50, 150): Islands.Away,
    (100, 300): Islands.Ironhill,
    (200, 400): Islands.Kyo,
    (300, 700): Islands.Heim,
    (500, 900): Islands.Atria,
    (700, math.inf): Islands.Eldara
}

def experience_for_level(level):
    if level - 2 >= len(experience_array):
        return experience_array[len(experience_array) - 1]
    return (0 if level - 2 < 0 else experience_array[level - 2])

def search_item(name: str, limit=10) -> List[Tuple[Item, int]]:
    search_result = thefuzz.process.extract(name, _items_names, limit=limit, scorer=thefuzz.fuzz.ratio)
    out_results = []
    for result, score in search_result:
        out_results.append((_items_name_data[result], score))
    return out_results

def get_item(item: str | Item | itemdefs.Items):
    if isinstance(item, Item):
        return item
    elif isinstance(item, str):
        if item.count('-') == 4:
            return _items_id_data.get(item)
        else:
            return _items_name_data.get(item)
    elif isinstance(item, itemdefs.Items):
        return _items_id_data.get(item.value)
    else:
        raise ValueError("bro...")

def get_all_items():
    return _items_list

def get_all_item_names():
    return _items_names

def get_raw_item_data():
    return _items

def get_island_for_level(level: int):
    for (min_lvl, max_lvl), island in reversed(island_ranges.items()):
        if min_lvl <= level <= max_lvl:
            return island

def get_material_for_level(level: int):
    for material, m_level in reversed(equipment_levels.items()):
        if m_level <= level:
            return material
        
load_local_item_data()

fighting_skills = (
    Skills.Attack, Skills.Defense, Skills.Strength, Skills.Health,
    Skills.Magic, Skills.Ranged, Skills.Healing, Skills.All, Skills.Melee
)
combat_skills = fighting_skills
resource_skills = (
    Skills.Mining, Skills.Gathering, Skills.Woodcutting, Skills.Farming,
    Skills.Fishing
)