from __future__ import annotations

from pydantic import BaseModel

from bot.clients import ravenfall_query as rq

from . import enums

GameSettings = rq.GameSettings
SoundSettings = rq.SoundSettings
UISettings = rq.UISettings
GraphicsSettings = rq.GraphicsSettings
QueryEngineSettings = rq.QueryEngineSettings
StreamLabelsSettings = rq.StreamLabelsSettings
PlayerObserveSeconds = rq.PlayerObserveSeconds
LootSettings = rq.LootSettings
GameConfig = rq.GameConfig
GameSession = rq.GameSession
GameMultiplier = rq.GameMultiplier
Boss = rq.Boss
Raid = rq.Raid
PlayerStat = rq.PlayerStat
PlayerStats = rq.PlayerStats
Player = rq.Player
Village = rq.Village
FerryCaptain = rq.FerryCaptain
FerryBoost = rq.FerryBoost
Ferry = rq.Ferry
IslandLevels = rq.IslandLevels
Island = rq.Island
Redeemable = rq.Redeemable


class Dungeon(rq.Dungeon):
    """Dungeon instance information.

    Contains dungeon status, player count, room information,
    and boss details.
    """

    stage: enums.DungeonStage = enums.DungeonStage.NONE


class RavenfallConfig(BaseModel):
    """Configuration model for Ravenfall integration."""

    middleman_base_url: str | None = None
    ravenfall_message_definitions_path: str = "./data/definitions.yaml"
    instances: list[RavenfallInstanceConfig]


class RavenfallInstanceConfig(BaseModel):
    """Configuration model for a Ravenfall instance."""

    twitch_id: str
    twitch_login: str
    query_server_base_url: str
    middleman_connection_id: str | None = None
