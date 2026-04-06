"""Ravenfall API client for querying game data.

This module provides a client for interacting with the Ravenfall game API,
including data models for various game entities and methods for retrieving
real-time game information.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, NamedTuple, cast

import aiohttp
from async_lru import alru_cache
from msgspec import DecodeError, Struct, field, json

import ravenpy
from ravenpy import Islands, Skills

# Configure logger for this module
logger = logging.getLogger(__name__)


class GameSettings(Struct):
    """Game configuration settings.

    Contains various game settings including player cache, camera controls,
    day/night cycle, and other gameplay configuration options.
    """

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
    """Sound configuration settings.

    Contains volume settings for music and raid horns.
    """

    music_volume: float = field(name="musicvolume")
    raid_horn_volume: float = field(name="raidhornvolume")


class UISettings(Struct):
    """User interface configuration settings.

    Contains settings for player name visibility and player list display.
    """

    player_names_visible: bool = field(name="playernamesvisible")
    player_list_size: float = field(name="playerlistsize")
    player_list_scale: float = field(name="playerlistscale")


class GraphicsSettings(Struct):
    """Graphics configuration settings.

    Contains settings for quality level, DPI scaling, and performance modes.
    """

    quality_level: int = field(name="qualitylevel")
    dpi_scale: float = field(name="dpiscale")
    potato_mode: bool = field(name="potatomode")
    auto_potato_mode: bool = field(name="autopotatomode")
    post_processing: bool = field(name="postprocessing")


class QueryEngineSettings(Struct):
    """Query engine configuration settings.

    Contains settings for the API query engine functionality.
    """

    enabled: bool
    always_return_array: bool = field(name="alwaysreturnarray")
    api_prefix: str = field(name="apiprefix")


class StreamLabelsSettings(Struct):
    """Stream labels configuration settings.

    Contains settings for stream label file output functionality.
    """

    enabled: bool
    save_text_files: bool = field(name="savetextfiles")
    save_json_files: bool = field(name="savejsonfiles")


class PlayerObserveSeconds(Struct):
    """Player observation time settings by user type.

    Contains observation time durations for different user roles
    and events (subscription, bits, etc.).
    """

    default: float
    subscriber: float
    moderator: float
    vip: float
    broadcaster: float
    on_subscription: float = field(name="onsubcription")
    on_cheered_bits: float = field(name="oncheeredbits")


class LootSettings(Struct):
    """Loot system configuration settings.

    Contains settings for loot drop behavior.
    """

    include_origin: bool = field(name="includeorigin")


class GameConfig(Struct):
    """Complete game configuration.

    Contains all game settings including game, sound, UI, graphics,
    query engine, stream labels, player observation, and loot settings.
    """

    game: GameSettings
    sound: SoundSettings
    ui: UISettings
    graphics: GraphicsSettings
    query_engine: QueryEngineSettings = field(name="queryengine")
    stream_labels: StreamLabelsSettings = field(name="streamlabels")
    player_observe_seconds: PlayerObserveSeconds = field(name="playerobserveseconds")
    loot: LootSettings


class GameSession(Struct):
    """Current game session information.

    Contains session status, player count, game version, and elapsed time.
    """

    authenticated: bool
    session_started: bool = field(name="sessionstarted")
    twitch_username: str | None = field(name="twitchusername")
    players: int
    game_version: str = field(name="gameversion")
    seconds_since_start: float = field(name="secondssincestart")


class GameMultiplier(Struct):
    """Game event multiplier information.

    Contains details about active game events that provide experience
    or other multipliers.
    """

    event_name: str | None = field(name="eventname")
    active: bool
    multiplier: float
    elapsed: float
    duration: float
    time_left: float = field(name="timeleft")
    start_time: str = field(name="starttime")
    end_time: str = field(name="endtime")


class Boss(Struct):
    """Boss entity information.

    Contains boss health and combat level information.
    """

    health: int
    max_health: int = field(name="maxhealth")
    health_percent: float = field(name="healthpercent")
    combat_level: int = field(name="combatlevel")


class Dungeon(Struct):
    """Dungeon instance information.

    Contains dungeon status, player count, room information,
    and boss details.
    """

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
    """Raid instance information.

    Contains raid status, player count, time remaining,
    and boss details.
    """

    started: bool
    players: int
    time_left: float = field(name="timeleft")
    count: int
    boss: Boss


class PlayerStat(Struct):
    """Individual player skill statistics.

    Contains skill level, current value, max level, and experience.
    """

    level: int
    current_value: int = field(name="currentvalue")
    max_level: int = field(name="maxlevel")
    experience: float


class PlayerStats(Struct):
    """Complete player statistics.

    Contains all skill statistics for a player including combat,
    gathering, and production skills.
    """

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
    """Player entity information.

    Contains player details including location, activity, coins,
    and complete statistics.
    """

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
    """Town boost information.

    Contains skill type and multiplier for town boosts.
    """

    skill: Skills
    multiplier: float


class TownBoostList(list[TownBoost]):
    """List of town boosts with serialization support.

    Extends list to provide serialization and deserialization
    methods for town boost data.
    """

    def serialize(self) -> str:
        """Serialize the boost list to a string format.

        Returns:
            str: Comma-separated list of boosts in "skill value%" format

        """
        return ", ".join([f"{boost.skill.value} {boost.multiplier}%" for boost in self])

    @staticmethod
    def deserialize(boost: str) -> TownBoostList:
        """Deserialize a string into a TownBoostList.

        Args:
            boost: String containing comma-separated boost data

        Returns:
            TownBoostList: List of parsed town boosts

        """
        if not boost or not boost.strip():
            return TownBoostList()

        boosts = TownBoostList()
        # Split by comma and process each boost
        boost_entries = boost.split(", ")

        for entry in boost_entries:
            entry_stripped = entry.strip()
            if not entry_stripped:
                continue

            parts = entry_stripped.split()
            if len(parts) < 2:  # noqa: PLR2004
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
    """Village information and status.

    Contains village name, level, tier, and active boosts.
    """

    name: str
    level: int
    tier: int
    boost: TownBoostList


class FerryCaptain(Struct):
    """Ferry captain information.

    Contains captain name and sailing level requirements.
    """

    name: str
    sailing_level: int = field(name="sailinglevel")


class FerryBoost(Struct):
    """Ferry boost status.

    Contains information about active ferry speed boosts.
    """

    is_active: bool = field(name="isactive")
    remaining_time: float = field(name="remainingtime")


class Ferry(Struct):
    """Ferry information and status.

    Contains destination, boost status, player count, and captain details.
    """

    destination: str
    boost: FerryBoost
    players: int
    captain: FerryCaptain


class IslandLevels(Struct):
    """Island level requirements.

    Contains skill and combat level requirements for islands.
    """

    skill: int
    combat: int


class IslandName(StrEnum):
    """Enumeration of available island names.

    Defines all possible island destinations in the game.
    """

    HOME = "Home"
    AWAY = "Away"
    IRONHILL = "Ironhill"
    KYO = "Kyo"
    HEIM = "Heim"
    ATRIA = "Atria"
    ELDARA = "Eldara"
    WAR = "War"


class Island(Struct):
    """Island information.

    Contains island name, player count, and level requirements.
    """

    name: IslandName
    players: int
    level: IslandLevels


class Redeemable(Struct):
    """Redeemable item information.

    Contains item details including cost, currency, and description.
    """

    item_id: str = field(name="itemid")
    name: str
    description: str | None
    currency: str
    cost: int


def enc_hook(obj: Any):  # pyright: ignore[reportAny, reportExplicitAny]
    """Encode object for JSON serialization.

    Args:
        obj: Object to encode

    Returns:
        Serialized representation of the object

    Raises:
        NotImplementedError: If object type is not supported

    """
    if isinstance(obj, TownBoostList):
        return obj.serialize()
    error_msg = f"Objects of type {type(obj)} are not supported"  # pyright: ignore[reportAny]
    raise NotImplementedError(error_msg)


def dec_hook(type_hint: type, obj: Any):  # pyright: ignore[reportAny, reportExplicitAny]
    """Decode object for JSON deserialization.

    Args:
        type_hint: Type hint for the object
        obj: Object to decode

    Returns:
        Deserialized object of the specified type

    Raises:
        NotImplementedError: If object type is not supported

    """
    if (type_hint is TownBoostList) and (isinstance(obj, str)):
        return TownBoostList.deserialize(obj)
    error_msg = f"Objects of type {type_hint} are not supported"
    raise NotImplementedError(error_msg)


enc_json = json.Encoder(enc_hook=enc_hook)


class CharacterStat:
    """Character skill statistic wrapper.

    Provides additional calculations and methods for character skill data
    including enchantment information and experience calculations.

    Attributes:
        skill: The skill type
        level: Current skill level
        level_exp: Experience at current level
        total_exp_for_level: Total experience needed for next level
        enchant_percent: Enchantment percentage
        enchant_levels: Number of enchantment levels

    """

    def __init__(self, skill: ravenpy.Skills, data: PlayerStat):
        """Initialize character statistic.

        Args:
            skill: The skill type
            data: Raw player stat data from the API

        """
        self.skill: Skills = skill
        self.level: int = data.level
        self.level_exp: float = data.experience
        self.total_exp_for_level: int = ravenpy.experience_for_level(self.level + 1)
        self.enchant_percent: float = data.max_level / data.level
        self.enchant_levels: int = data.max_level - data.level

    def _add_enchant(self, percent: float):
        """Add enchantment percentage to the skill.

        Args:
            percent: Enchantment percentage to add

        """
        self.enchant_percent += percent
        self.enchant_levels = round(self.level * self.enchant_percent)


class Character:
    """Character data wrapper with enhanced functionality.

    Provides additional methods and calculations for character data
    including training status, skill lookups, and combat calculations.

    Attributes:
        id: Character ID
        char_id: Alias for character ID
        user_name: Character name
        coins: Coin amount
        combat_level: Calculated combat level
        hp: Current health points
        training: Current training skill
        island: Current island location
        and various skill statistics...

    """

    def __init__(self, data: Player):
        """Initialize character from player data.

        Args:
            data: Raw player data from the API

        """
        self._raw: Player = data
        self.time_received: datetime = datetime.now(UTC)
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
        self.woodcutting: CharacterStat = CharacterStat(
            Skills.Woodcutting, data.stats.woodcutting
        )
        self.fishing: CharacterStat = CharacterStat(Skills.Fishing, data.stats.fishing)
        self.mining: CharacterStat = CharacterStat(Skills.Mining, data.stats.mining)
        self.crafting: CharacterStat = CharacterStat(Skills.Crafting, data.stats.crafting)
        self.cooking: CharacterStat = CharacterStat(Skills.Cooking, data.stats.cooking)
        self.farming: CharacterStat = CharacterStat(Skills.Farming, data.stats.farming)
        self.slayer: CharacterStat = CharacterStat(Skills.Slayer, data.stats.slayer)
        self.sailing: CharacterStat = CharacterStat(Skills.Sailing, data.stats.sailing)
        self.healing: CharacterStat = CharacterStat(Skills.Healing, data.stats.healing)
        self.gathering: CharacterStat = CharacterStat(
            Skills.Gathering, data.stats.gathering
        )
        self.alchemy: CharacterStat = CharacterStat(Skills.Alchemy, data.stats.alchemy)
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
            if data.training == "Fighting":
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
                        enchantment="",
                    )
                    self.target_item = inv_item

        if not self.training and ((not self.island) or self.is_sailing):
            self.training = Skills.Sailing

        self.training_stats: list[CharacterStat] = []
        if self.training:
            if self.training in (Skills.All, Skills.Health):
                self.training_stats.extend(
                    [self.health, self.attack, self.defense, self.strength]
                )
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
        """Get character statistic for a specific skill.

        Args:
            skill: The skill to retrieve

        Returns:
            CharacterStat: The skill statistic for this character

        """
        return self._skill_dict[skill]


class RavenfallTimeoutError(Exception):
    """Exception raised when a query times out."""


class RavenfallConnectionError(Exception):
    """Exception raised when unable to connect to the API."""


class RavenfallBadHostError(Exception):
    """Exception raised when Ravenfall returns an "invalid host" error."""


class RavenfallQueryError(Exception):
    """Exception raised when the API returns an error."""


class EmptyResponseError(Exception):
    """Exception raised when Ravenfall unexpectedly returns an empty value."""


CACHE_TTL = 0.1


class RavenfallClient:
    """Client for interacting with the Ravenfall game API.

    Provides methods for querying various game data including players,
    sessions, islands, and other game entities.

    Attributes:
        base_url: Base URL for the API endpoint
        logger: Logger instance for this client

    """

    def __init__(self, base_url: str):
        """Initialize the Ravenfall client.

        Args:
            base_url: Base URL for the Ravenfall API

        """
        self.base_url: str = base_url.rstrip("/")
        self.logger: logging.Logger = logging.getLogger(__name__)
        self.default_request_timeout: float = 3

    async def _query_type[T](
        self, query: str, out_type: type[T], timeout_seconds: float | None = None
    ) -> list[T]:
        if timeout_seconds is None:
            timeout_seconds = self.default_request_timeout
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds)
                ) as session,
                session.get(f"{self.base_url}/{query}") as r,
            ):
                text = await r.text()
                if text.startswith('{"error'):
                    err_text = text[9:-2]
                    self.logger.error(
                        f"Ravenfall query failed in {self.base_url}: {err_text}"
                    )
                    raise RavenfallQueryError(err_text)
                if text.startswith("<h1>Bad"):
                    msg = (
                        'Ravenfall returned an "invalid host" error. '
                        "Make sure the passed URL's hostname matches "
                        "'queryEngineApiPrefix' in Ravenfall's config file. "
                        f"(passed URL: {self.base_url})"
                    )
                    raise RavenfallBadHostError(msg)
                if text != "{}":
                    try:
                        data = cast(
                            "T | list[T]",
                            json.decode(
                                text,
                                type=out_type | list[out_type],
                                dec_hook=dec_hook,
                            ),
                        )
                    except DecodeError:
                        err_text = f"Failed to decode to json: {text}"
                        raise RavenfallQueryError(err_text) from None
                else:
                    data = None
        except TimeoutError:
            self.logger.error(
                f"Timeout fetching Ravenfall query from {self.base_url}/{query}"
            )
            raise RavenfallTimeoutError from None
        except aiohttp.ClientConnectorError:
            self.logger.error(f"Error fetching Ravenfall query from {self.base_url}")
            raise RavenfallConnectionError from None
        except Exception:
            self.logger.exception(f"Error fetching Ravenfall query from {self.base_url}")
            raise
        else:
            if data is None:
                return []
            if not isinstance(data, list):
                if not data:
                    return []
                return [data]
            return data  # pyright: ignore[reportUnknownVariableType]

    # @alru_cache(ttl=CACHE_TTL)
    async def get_session(self, *, timeout_seconds: float | None = None) -> GameSession:
        """Get the current game session.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            GameSession: The current game session

        """
        response = await self._query_type(
            query="select * from session",
            out_type=GameSession,
            timeout_seconds=timeout_seconds,
        )
        if not response:
            raise EmptyResponseError
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_ferry(self, *, timeout_seconds: float | None = None) -> Ferry:
        """Get the current ferry status.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            Ferry: The current ferry status

        """
        response = await self._query_type(
            query="select * from ferry", out_type=Ferry, timeout_seconds=timeout_seconds
        )
        if not response:
            raise EmptyResponseError
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_players(self, *, timeout_seconds: float | None = None) -> list[Player]:
        """Get all players in the game.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            list[Player]: List of all players

        """
        return await self._query_type(
            query="select * from players",
            out_type=Player,
            timeout_seconds=timeout_seconds,
        )

    @alru_cache(ttl=CACHE_TTL)
    async def get_village(self, *, timeout_seconds: float | None = None) -> Village:
        """Get the current village status.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            Village: The current village status

        """
        response = await self._query_type(
            query="select * from village",
            out_type=Village,
            timeout_seconds=timeout_seconds,
        )
        if not response:
            raise EmptyResponseError
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_multiplier(
        self, *, timeout_seconds: float | None = None
    ) -> GameMultiplier:
        """Get the current game multiplier.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            GameMultiplier: The current game multiplier

        """
        response = await self._query_type(
            query="select * from multiplier",
            out_type=GameMultiplier,
            timeout_seconds=timeout_seconds,
        )
        if not response:
            raise EmptyResponseError
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_dungeon(self, *, timeout_seconds: float | None = None) -> Dungeon:
        """Get the current dungeon status.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            Dungeon: The current dungeon status

        """
        response = await self._query_type(
            query="select * from dungeon",
            out_type=Dungeon,
            timeout_seconds=timeout_seconds,
        )
        if not response:
            raise EmptyResponseError
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_raid(self, *, timeout_seconds: float | None = None) -> Raid:
        """Get the current raid status.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            Raid: The current raid status

        """
        response = await self._query_type(
            query="select * from raid", out_type=Raid, timeout_seconds=timeout_seconds
        )
        if not response:
            raise EmptyResponseError
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_observed(
        self, *, timeout_seconds: float | None = None
    ) -> Player | None:
        """Get the currently observed player.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            Player: The currently observed player

        """
        response = await self._query_type(
            query="select * from observed",
            out_type=Player,
            timeout_seconds=timeout_seconds,
        )
        if not response:
            return None
        return response[0]

    @alru_cache(ttl=CACHE_TTL)
    async def get_islands(self, *, timeout_seconds: float | None = None) -> list[Island]:
        """Get info on all islands.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            list[Island]: List of all islands

        """
        return await self._query_type(
            query="select * from islands",
            out_type=Island,
            timeout_seconds=timeout_seconds,
        )

    @alru_cache(ttl=CACHE_TTL)
    async def get_redeemables(
        self, *, timeout_seconds: float | None = None
    ) -> list[Redeemable]:
        """Get all redeemables.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            list[Redeemable]: List of all redeemables

        """
        return await self._query_type(
            query="select * from redeemables",
            out_type=Redeemable,
            timeout_seconds=timeout_seconds,
        )

    @alru_cache(ttl=CACHE_TTL)
    async def get_config(self, *, timeout_seconds: float | None = None) -> GameConfig:
        """Get the game configuration.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            GameConfig: The game configuration

        """
        response = await self._query_type(
            query="select * from settings",
            out_type=GameConfig,
            timeout_seconds=timeout_seconds,
        )
        if not response:
            raise EmptyResponseError
        return response[0]
