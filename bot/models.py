from typing import TypedDict, Dict, Literal, Union, NamedTuple, Optional, List
from uuid import UUID
from enum import Enum

class TownBoost(NamedTuple):
    skill: str
    multiplier: float

class Channel(TypedDict):
    channel_id: str
    channel_name: str
    rf_query_url: str
    custom_town_msg: str
    ravenbot_prefix: str | list | tuple
    welcome_message: str
    receive_global_alerts: bool
    sandboxie_box: str
    ravenfall_start_script: str
    auto_restart: bool
    event_notifications: bool
    restart_period: int

class GameSession(TypedDict):
    authenticated: bool
    sessionstarted: bool
    twitchusername: str
    players: int
    gameversion: str
    secondssincestart: float

class GameMultiplier(TypedDict):
    eventname: str
    active: bool
    multiplier: float
    elapsed: float
    duration: float
    timeleft: float
    starttime: str
    endtime: str

class Boss(TypedDict):
    health: int
    maxhealth: int
    healthpercent: int
    combatlevel: int
    
class Dungeon(TypedDict):
    started: bool
    secondsuntilstart: int
    name: str
    room: int
    players: int
    playersalive: int
    enemies: int
    enemiesalive: int
    elapsed: float
    count: int
    boss: Boss

class Raid(TypedDict):
    started: bool
    players: int
    timeleft: float
    count: int
    boss: Boss
    
class PlayerStat(TypedDict):
    level: int
    currentvalue: int
    maxlevel: int
    experience: float

class Player(TypedDict):
    id: str
    name: str
    training: str
    taskargument: str
    island: str
    sailing: bool
    resting: bool
    restedtime: float
    inarena: bool
    induel: bool
    indungeon: bool
    inraid: bool
    coins: int
    commandidletime: float
    stats: Dict[Literal[
        "combatlevel", "attack", "defense", "strength", "health", "woodcutting",
        "fishing", "mining", "crafting", "cooking", "farming", "slayer", "magic", "ranged",
        "sailing", "healing", "gathering", "alchemy"
    ], Union[int, PlayerStat]]

class Village(TypedDict):
    name: str
    level: int
    tier: int
    boost: str

class FerryCaptain(TypedDict):
    name: str
    sailinglevel: int

class FerryBoost(TypedDict):
    isactive: bool
    remainingtime: float

class Ferry(TypedDict):
    destination: str
    boost: FerryBoost
    players: int
    captain: FerryCaptain

class Sender(TypedDict):
    Id: UUID
    CharacterId: UUID
    Username: str
    DisplayName: str
    Color: str
    Platform: str
    PlatformId: str
    IsBroadcaster: bool
    IsModerator: bool
    IsSubscriber: bool
    IsVip: bool
    IsGameAdministrator: bool
    IsGameModerator: bool
    SubTier: int
    Identifier: str

class RavenBotMessage(TypedDict):
    """Represents a message from RavenBot with its metadata."""
    Identifier: str
    Sender: Sender
    Content: str
    CorrelationId: UUID


class Recipient(TypedDict):
    """Represents the recipient information in a Ravenfall message."""
    UserId: UUID
    CharacterId: UUID
    Platform: str
    PlatformId: str
    PlatformUserName: str


class RavenfallMessage(TypedDict):
    """Represents a message received from Ravenfall."""
    Identifier: str  # e.g., "message"
    Recipent: Recipient
    Format: str  # Format string for the message
    Args: List[str]  # Arguments to be inserted into the format string
    Tags: List[str]  # Any tags associated with the message
    Category: str  # Message category (if any)
    CorrelationId: UUID  # For tracking the message

class RFChannelEvent(Enum):
    NONE = 0
    DUNGEON = 1
    RAID = 2

class RFChannelSubEvent(Enum):
    NONE = 0
    DUNGEON_PREPARE = 1
    DUNGEON_READY = 2
    DUNGEON_STARTED = 3
    DUNGEON_BOSS = 4
    RAID = 5

class RFMiddlemanMessage(TypedDict):
    source: str
    client_addr: str
    server_addr: str
    connection_id: str
    timestamp: str
    message: RavenfallMessage | RavenBotMessage