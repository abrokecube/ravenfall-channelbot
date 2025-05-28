from typing import TypedDict, Dict, Literal, Union, NamedTuple
from dataclasses import dataclass

class TownBoost(NamedTuple):
    skill: str
    multiplier: float

class Channel(TypedDict):
    channel_id: str
    channel_name: str
    rf_query_url: str
    custom_town_msg: str
    ravenbot_prefix: str
    welcome_message: str
    recieve_global_alerts: bool

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
    town: str

class Village(TypedDict):
    name: str
    level: int
    tier: int
    boost: str

class FerryCaptain(TypedDict):
    name: str
    sailinglevel: int

class Ferry(TypedDict):
    destination: str
    players: int
    captain: FerryCaptain
