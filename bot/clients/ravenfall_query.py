import ravenpy
from datetime import datetime, timezone, timedelta
from ravenpy import Skills, Islands
import aiohttp
import logging
from typing import Any, NamedTuple
import asyncio
from msgspec import Struct, field, json
from enum import StrEnum
from async_lru import alru_cache

# Configure logger for this module
logger = logging.getLogger(__name__)

class GameSettings(Struct):
    player_cache_expiry_time_index: int = field(name="playercacheexpirytimeindex")
    camera_rotation_speed: float = field(name="camerarotationspeed")
    day_night_time: float = field(name="daynighttime")
    day_night_cycle_enabled: bool = field(name="daynightcycleenabled")
    real_time_day_night_cycle: bool = field(name="realtimedaynightcycle")
    auto_kick_afk_players: bool = field(name="autokickafkplayers")
    local_bot_server_disabled: bool = field(name="localbotserverdisabled")
    alert_expired_state_cache_in_chat: bool = field(name="alertexpiredstatecacheinchat")
    can_observe_empty_islands: bool = field(name="canobserveemptyislands")
    player_boost_requirement: int = field(name="playerboostrequirement")
    item_drop_message_type: int = field(name="itemdropmessagetype")
    path_finding_quality_settings: int = field(name="pathfindingqualitysettings")
    local_bot_port: int = field(name="localbotport")
    island_observe_seconds: float = field(name="islandobserveseconds")

class SoundSettings(Struct):
    music_volume: float = field(name="musicvolume")
    raid_horn_volume: float = field(name="raidhornvolume")

class UISettings(Struct):
    player_names_visible: bool = field(name="playernamesvisible")
    player_list_size: float = field(name="playerlistsize")
    player_list_scale: float = field(name="playerlistscale")

class GraphicsSettings(Struct):
    quality_level: int = field(name="qualitylevel")
    dpi_scale: float = field(name="dpiscale")
    potato_mode: bool = field(name="potatomode")
    auto_potato_mode: bool = field(name="autopotatomode")
    post_processing: bool = field(name="postprocessing")

class QueryEngineSettings(Struct):
    enabled: bool
    always_return_array: bool = field(name="alwaysreturnarray")
    api_prefix: str = field(name="apiprefix")

class StreamLabelsSettings(Struct):
    enabled: bool
    save_text_files: bool = field(name="savetextfiles")
    save_json_files: bool = field(name="savejsonfiles")

class PlayerObserveSeconds(Struct):
    default: float
    subscriber: float
    moderator: float
    vip: float
    broadcaster: float
    on_subscription: float = field(name="onsubcription")
    on_cheered_bits: float = field(name="oncheeredbits")

class LootSettings(Struct):
    include_origin: bool = field(name="includeorigin")

class GameConfig(Struct):
    game: GameSettings
    sound: SoundSettings
    ui: UISettings
    graphics: GraphicsSettings
    query_engine: QueryEngineSettings = field(name="queryengine")
    stream_labels: StreamLabelsSettings = field(name="streamlabels")
    player_observe_seconds: PlayerObserveSeconds = field(name="playerobserveseconds")
    loot: LootSettings


class GameSession(Struct):
    authenticated: bool
    session_started: bool = field(name="sessionstarted")
    twitch_username: str | None = field(name="twitchusername")
    players: int
    game_version: str = field(name="gameversion")
    seconds_since_start: float = field(name="secondssincestart")


class GameMultiplier(Struct):
    event_name: str | None = field(name="eventname")
    active: bool
    multiplier: float
    elapsed: float
    duration: float
    time_left: float = field(name="timeleft")
    start_time: str = field(name="starttime")
    end_time: str = field(name="endtime")


class Boss(Struct):
    health: int
    max_health: int = field(name="maxhealth")
    health_percent: float = field(name="healthpercent")
    combat_level: int = field(name="combatlevel")

class Dungeon(Struct):
    started: bool
    seconds_until_start: float = field(name="secondsuntilstart")
    name: str
    room: int
    players: int
    players_alive: int = field(name="playersalive")
    enemies: int
    enemies_alive: int = field(name="enemiesalive")
    elapsed: float
    count: int
    boss: Boss

class Raid(Struct):
    started: bool
    players: int
    time_left: float = field(name="timeleft")
    count: int
    boss: Boss


class PlayerStat(Struct):
    level: int
    current_value: int = field(name="currentvalue")
    max_level: int = field(name="maxlevel")
    experience: float

class PlayerStats(Struct):
    combat_level: int = field(name="combatlevel")
    attack: PlayerStat
    defense: PlayerStat
    strength: PlayerStat
    health: PlayerStat
    woodcutting: PlayerStat
    fishing: PlayerStat
    mining: PlayerStat
    crafting: PlayerStat
    cooking: PlayerStat
    farming: PlayerStat
    slayer: PlayerStat
    magic: PlayerStat
    ranged: PlayerStat
    sailing: PlayerStat
    healing: PlayerStat
    gathering: PlayerStat
    alchemy: PlayerStat

class Player(Struct):
    id: str
    name: str
    training: str
    task_argument: str | None = field(name="taskargument")
    island: str
    sailing: bool
    resting: bool
    rested_time: float = field(name="restedtime")
    in_arena: bool = field(name="inarena")
    in_duel: bool = field(name="induel")
    in_dungeon: bool = field(name="indungeon")
    in_raid: bool = field(name="inraid")
    coins: int
    command_idle_time: float = field(name="commandidletime")
    stats: PlayerStats


class TownBoost(NamedTuple):
    skill: Skills
    multiplier: float

class TownBoostList(list[TownBoost]):
    def serialize(self) -> str:
        return ", ".join([f"{boost.skill.value} {boost.multiplier}%" for boost in self])

    @staticmethod
    def deserialize(boost: str) -> 'TownBoostList':
        if not boost or not boost.strip():
            return TownBoostList()
        
        boosts = TownBoostList()
        # Split by comma and process each boost
        boost_entries = boost.split(", ")
        
        for entry in boost_entries:
            entry = entry.strip()
            if not entry:
                continue
                
            parts = entry.split()
            if len(parts) < 2:
                continue
                
            boost_stat = parts[0]
            boost_value_str = " ".join(parts[1:]).rstrip("%")
            try:
                boost_value = float(boost_value_str)
                boosts.append(TownBoost(Skills[boost_stat], boost_value))
            except (ValueError, KeyError):
                continue
        
        return boosts

class Village(Struct):
    name: str
    level: int
    tier: int
    boost: TownBoostList


class FerryCaptain(Struct):
    name: str
    sailing_level: int = field(name="sailinglevel")

class FerryBoost(Struct):
    is_active: bool = field(name="isactive")
    remaining_time: float = field(name="remainingtime")

class Ferry(Struct):
    destination: str
    boost: FerryBoost
    players: int
    captain: FerryCaptain


class IslandLevels(Struct):
    skill: int
    combat: int

class IslandName(StrEnum):
    HOME = "Home"
    AWAY = "Away"
    IRONHILL = "Ironhill"
    KYO = "Kyo"
    HEIM = "Heim"
    ATRIA = "Atria"
    ELDARA = "Eldara"
    WAR = "War"

class Island(Struct):
    name: IslandName
    players: int
    level: IslandLevels


class Redeemable(Struct):
    item_id: str = field(name="itemid")
    name: str
    description: str | None
    currency: str
    cost: int


def enc_hook(obj: Any):  # pyright: ignore[reportAny, reportExplicitAny]
    if isinstance(obj, TownBoostList):
        return obj.serialize()
    else:
        raise NotImplementedError(f"Objects of type {type(obj)} are not supported")


def dec_hook(type: type, obj: Any):  # pyright: ignore[reportAny, reportExplicitAny]
    if type is TownBoostList:
        return TownBoostList.deserialize(obj)
    else:
        raise NotImplementedError(f"Objects of type {type} are not supported")

enc_json = json.Encoder(enc_hook=enc_hook)

class CharacterStat:
    def __init__(self, skill: ravenpy.Skills, data: PlayerStat):
        self.skill: Skills = skill
        self.level: int = data.level
        self.level_exp: float = data.experience
        self.total_exp_for_level: int = ravenpy.experience_for_level(self.level+1)
        self.enchant_percent: float = data.max_level/data.level
        self.enchant_levels: int = data.max_level - data.level

    def _add_enchant(self, percent: float):
        self.enchant_percent += percent
        self.enchant_levels = round(self.level * self.enchant_percent)

class Character:
    def __init__(self, data: Player):
        self._raw: Player = data
        self.time_received: datetime = datetime.now(timezone.utc)
        self.id: str = data.id
        self.char_id: str = self.id
        self.user_name: str = data.name
        self.coins: int = data.coins

        self.attack: CharacterStat = CharacterStat(Skills.Attack, data.stats.attack)
        self.defense: CharacterStat = CharacterStat(Skills.Defense, data.stats.defense)
        self.strength: CharacterStat = CharacterStat(Skills.Strength, data.stats.strength)
        self.health: CharacterStat = CharacterStat(Skills.Health, data.stats.health)
        self.magic: CharacterStat = CharacterStat(Skills.Magic, data.stats.magic)
        self.ranged: CharacterStat = CharacterStat(Skills.Ranged, data.stats.ranged)
        self.woodcutting: CharacterStat = CharacterStat(Skills.Woodcutting, data.stats.woodcutting)
        self.fishing: CharacterStat = CharacterStat(Skills.Fishing, data.stats.fishing)
        self.mining: CharacterStat = CharacterStat(Skills.Mining, data.stats.mining)
        self.crafting: CharacterStat = CharacterStat(Skills.Crafting, data.stats.crafting)
        self.cooking: CharacterStat = CharacterStat(Skills.Cooking, data.stats.cooking)
        self.farming: CharacterStat = CharacterStat(Skills.Farming, data.stats.farming)
        self.slayer: CharacterStat = CharacterStat(Skills.Slayer, data.stats.slayer)
        self.sailing: CharacterStat = CharacterStat(Skills.Sailing, data.stats.sailing)
        self.healing: CharacterStat = CharacterStat(Skills.Healing, data.stats.healing)
        self.gathering: CharacterStat = CharacterStat(Skills.Gathering, data.stats.gathering)
        self.alchemy: CharacterStat = CharacterStat(Skills.Alchemy, data.stats.alchemy)
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
        self.hp: int = data.stats.health.current_value
        self.in_raid: bool = data.in_raid
        self.in_arena: bool = data.in_arena
        self.in_dungeon: bool = data.in_dungeon
        self.in_onsen: bool = data.resting
        self.is_resting: bool = self.in_onsen
        self.is_sailing: bool = data.sailing

        self.training: Skills | None = None
        self.island: Islands | None = Islands(data.island) if data.island != "" else None

        self.rested_time: timedelta = timedelta(seconds=int(data.rested_time))
        self.target_item: ravenpy.CharacterItem | None = None
        if data.task_argument is not None:
            if (data.training == "Fighting"):
                task_arg = data.task_argument.capitalize()
                replace = ravenpy.fighting_replacements.get(task_arg)
                if replace:
                    task_arg = replace
                self.training = Skills[task_arg]
            elif (not data.training) or data.training.lower() == "none":
                pass
            else:
                self.training = Skills[data.training.capitalize()]
                result = ravenpy.get_item(data.task_argument)
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

class TimeoutError(Exception):
    pass

class ConnectionError(Exception):
    pass

class QueryException(BaseException):
    pass

CACHE_TTL = 0.2

class RavenfallClient:
    def __init__(self, base_url: str):
        self.base_url: str = base_url.rstrip('/')
        self.logger: logging.Logger = logging.getLogger(__name__)

    async def _query(self, query: str, timeout: int = 3) -> list[dict[str, Any]]:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            try:
                r = await session.get(f"{self.base_url}/{query}")
                data = await r.json()
                if isinstance(data, dict):
                    if not data:
                        return []
                    if "error" in data:
                        self.logger.error(f"Ravenfall query failed in {self.base_url}: {data['error']}")
                        raise QueryException(data['error'])
                    return [data]
                return data
            except asyncio.TimeoutError:
                self.logger.error(f"Timeout fetching Ravenfall query from {self.base_url}")
                raise TimeoutError()
            except aiohttp.ClientConnectorError as e:
                self.logger.error(f"Error fetching Ravenfall query from {self.base_url}: {e}")
                raise ConnectionError()
            except Exception as e:
                self.logger.error(f"Error fetching Ravenfall query from {self.base_url}: {e}", exc_info=True)
                raise

    async def _query_type[T](self, query: str, out_type: type[T], timeout: int = 3) -> list[T]:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            try:
                async with session.get(f"{self.base_url}/{query}") as r:
                    text = await r.text()
                    if text.startswith('{"error'):
                        err_text = text[9:-2]
                        self.logger.error(f"Ravenfall query failed in {self.base_url}: {err_text}")
                        raise QueryException(err_text)
                    data = json.decode(text, type=out_type | list[out_type], dec_hook=dec_hook)
                if not isinstance(data, list):
                    if not data:
                        return []
                    return [data]
                return data
            except asyncio.TimeoutError:
                self.logger.error(f"Timeout fetching Ravenfall query from {self.base_url}")
                raise TimeoutError()
            except aiohttp.ClientConnectorError as e:
                self.logger.error(f"Error fetching Ravenfall query from {self.base_url}: {e}")
                raise ConnectionError()
            except Exception as e:
                self.logger.error(f"Error fetching Ravenfall query from {self.base_url}: {e}", exc_info=True)
                raise

    @alru_cache(ttl=CACHE_TTL)
    async def get_session(self, *, timeout: int = 3) -> GameSession:
        """
        Get the current game session.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            GameSession: The current game session
        """
        response = await self._query_type(query="select * from session", out_type=GameSession, timeout=timeout)
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_ferry(self, *, timeout: int = 3) -> Ferry:
        """
        Get the current ferry status.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            Ferry: The current ferry status
        """
        response = await self._query_type(query="select * from ferry", out_type=Ferry, timeout=timeout)
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_players(self, *, timeout: int = 3) -> list[Player]:
        """
        Get all players in the game.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            list[Player]: List of all players
        """
        response = await self._query_type(query="select * from players", out_type=Player, timeout=timeout)
        return response

    @alru_cache(ttl=CACHE_TTL)
    async def get_village(self, *, timeout: int = 3) -> Village:
        """
        Get the current village status.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            Village: The current village status
        """
        response = await self._query_type(query="select * from village", out_type=Village, timeout=timeout)
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_multiplier(self, *, timeout: int = 3) -> GameMultiplier:
        """
        Get the current game multiplier.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            GameMultiplier: The current game multiplier
        """
        response = await self._query_type(query="select * from multiplier", out_type=GameMultiplier, timeout=timeout)
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_dungeon(self, *, timeout: int = 3) -> Dungeon:
        """
        Get the current dungeon status.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            Dungeon: The current dungeon status
        """
        response = await self._query_type(query="select * from dungeon", out_type=Dungeon, timeout=timeout)
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_raid(self, *, timeout: int = 3) -> Raid:
        """
        Get the current raid status.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            Raid: The current raid status
        """
        response = await self._query_type(query="select * from raid", out_type=Raid, timeout=timeout)
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_observed(self, *, timeout: int = 3) -> Player:
        """
        Get the currently observed player.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            Player: The currently observed player
        """
        response = await self._query_type(query="select * from observed", out_type=Player, timeout=timeout)
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_islands(self, *, timeout: int = 3) -> list[Island]:
        """
        Get info on all islands.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            list[Island]: List of all islands
        """
        response = await self._query_type(query="select * from islands", out_type=Island, timeout=timeout)
        return response

    @alru_cache(ttl=CACHE_TTL)
    async def get_redeemables(self, *, timeout: int = 3) -> list[Redeemable]:
        """
        Get all redeemables.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            list[Redeemable]: List of all redeemables
        """
        response = await self._query_type(query="select * from redeemables", out_type=Redeemable, timeout=timeout)
        return response

    @alru_cache(ttl=CACHE_TTL)
    async def get_config(self, *, timeout: int = 3) -> GameConfig:
        """
        Get the game configuration.
        
        Args:
            timeout: Timeout in seconds for the request
            
        Returns:
            GameConfig: The game configuration
        """
        response = await self._query_type(query="select * from settings", out_type=GameConfig, timeout=timeout)
        return response[0]

