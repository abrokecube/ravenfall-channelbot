
import ravenpy
from .models import *
from datetime import datetime, timezone, timedelta
from ravenpy import Skills, Islands
import aiohttp
import logging
from typing import Any, cast

# Configure logger for this module
logger = logging.getLogger(__name__)

class CharacterStat:
    def __init__(self, skill: ravenpy.Skills, data: PlayerStat):
        self.skill: Skills = skill
        self.level: int = data['level']
        self.level_exp: float = data['experience']
        self.total_exp_for_level: int = ravenpy.experience_for_level(self.level+1)
        self.enchant_percent: float = data['maxlevel']/data['level']
        self.enchant_levels: int = data['maxlevel'] - data['level']

    def _add_enchant(self, percent: float):
        self.enchant_percent += percent
        self.enchant_levels = round(self.level * self.enchant_percent)

class Character:
    def __init__(self, data: Player):
        self._raw: Player = data
        self.time_received: datetime = datetime.now(timezone.utc)
        self.id: str = data["id"]
        self.char_id: str = self.id
        self.user_name: str = data['name']
        self.coins: int = data['coins']
        
        self.attack: CharacterStat = CharacterStat(Skills.Attack, data['stats']['attack'])
        self.defense: CharacterStat = CharacterStat(Skills.Defense, data['stats']['defense'])
        self.strength: CharacterStat = CharacterStat(Skills.Strength, data['stats']['strength'])
        self.health: CharacterStat = CharacterStat(Skills.Health, data['stats']['health'])
        self.magic: CharacterStat = CharacterStat(Skills.Magic, data['stats']['magic'])
        self.ranged: CharacterStat = CharacterStat(Skills.Ranged, data['stats']['ranged'])
        self.woodcutting: CharacterStat = CharacterStat(Skills.Woodcutting, data['stats']['woodcutting'])
        self.fishing: CharacterStat = CharacterStat(Skills.Fishing, data['stats']['fishing'])
        self.mining: CharacterStat = CharacterStat(Skills.Mining, data['stats']['mining'])
        self.crafting: CharacterStat = CharacterStat(Skills.Crafting, data['stats']['crafting'])
        self.cooking: CharacterStat = CharacterStat(Skills.Cooking, data['stats']['cooking'])
        self.farming: CharacterStat = CharacterStat(Skills.Farming, data['stats']['farming'])
        self.slayer: CharacterStat = CharacterStat(Skills.Slayer, data['stats']['slayer'])
        self.sailing: CharacterStat = CharacterStat(Skills.Sailing, data['stats']['sailing'])
        self.healing: CharacterStat = CharacterStat(Skills.Healing, data['stats']['healing'])
        self.gathering: CharacterStat = CharacterStat(Skills.Gathering, data['stats']['gathering'])
        self.alchemy: CharacterStat = CharacterStat(Skills.Alchemy, data['stats']['alchemy'])
        self.combat_level: int = int(((self.attack.level + self.defense.level + self.health.level + self.strength.level) / 4) + ((self.ranged.level + self.magic.level + self.healing.level) / 8))
        self.stats: list[CharacterStat] = [
            self.attack, self.defense, self.strength, self.health, self.magic,
            self.ranged, self.woodcutting, self.fishing, self.mining, self.crafting,
            self.cooking, self.farming, self.slayer, self.sailing, self.healing,
            self.gathering, self.alchemy
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
            Skills.Alchemy: self.alchemy
        }
        self.hp: int = data['stats']["health"]["currentvalue"]
        self.in_raid: bool = data['inraid']
        self.in_arena: bool = data['inarena']
        self.in_dungeon: bool = data['indungeon']
        self.in_onsen: bool = data['resting']
        self.is_resting: bool = self.in_onsen
        self.is_sailing: bool = data['sailing']
        
        self.training: Skills | None = None
        self.island: Islands | None = Islands(data['island']) if data['island'] != "" else None
   
        self.rested_time: timedelta = timedelta(seconds=int(data['restedtime']))
        self.target_item: ravenpy.CharacterItem | None = None
        if data['training'] == "Fighting":
            task_arg = data['taskargument'].capitalize()
            replace = ravenpy.fighting_replacements.get(task_arg)
            if replace:
                task_arg = replace
            self.training = Skills[task_arg]
        elif (not data['training']) or data['training'].lower() == "none":
            pass
        else:
            self.training = Skills[data['training'].capitalize()]
            result = ravenpy.get_item(data['taskargument'])
            if result:
                target_item = result
                inv_item = ravenpy.CharacterItem(
                    itemId=target_item.id,
                    amount=0,
                    equipped=False,
                    soulbound=False,
                    enchantment=''
                )
                self.target_item = inv_item

        if not self.training:
            if (not self.island) or self.is_sailing:
                self.training = Skills.Sailing
                
        self.training_stats: list[CharacterStat] = []
        if self.training:
            if self.training in (Skills.All, Skills.Health):
                self.training_stats.extend([self.health, self.attack, self.defense, self.strength])
            else:
                self.training_stats.append(self.get_skill(self.training))
                if self.training in ravenpy.combat_skills:
                    self.training_stats.append(self.health)

            if self.in_raid or self.in_dungeon:
                self.training_stats.append(self.slayer)
        self.training_skills: list[Skills] = []
        for char_stat in self.training_stats:
            self.training_skills.append(char_stat.skill)
            
    def get_skill(self, skill: Skills):
        return self._skill_dict[skill]

async def get_ravenfall_query(url: str, query: str, timeout: int = 5) -> dict[str, Any] | list[Any] | None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        try:
            r = await session.get(f"{url}/{query}")
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Error fetching Ravenfall query from {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching Ravenfall query from {url}: {e}", exc_info=True)
            return None
        data = cast(dict[str, Any] | list[Any], await r.json())
    return data
