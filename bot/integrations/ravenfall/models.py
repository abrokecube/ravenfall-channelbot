from dataclasses import dataclass
from string import Formatter
from typing import ClassVar

from pydantic import BaseModel, Field

from bot.clients import ravenfall_query as rq
from bot.clients.ravenfall_middleman import Recipient
from bot.services.config_service import ConfigModel

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


class RavenfallInstanceConfig(BaseModel):
    """Configuration model for a Ravenfall instance."""

    twitch_id: str
    twitch_login: str
    query_server_base_url: str
    middleman_connection_id: str | None = None
    translations_path: str | None = None


class RavenfallConfig(ConfigModel):
    """Configuration model for Ravenfall integration."""

    config_table_name: ClassVar[str | None] = "integrations.ravenfall"

    username: str
    password: str
    middleman_base_url: str | None = None
    ravenfall_message_definitions_path: str = "./data/definitions.yaml"
    instances: list[RavenfallInstanceConfig] = Field(default_factory=list)


class _SafeDict(dict[str, object]):
    def __missing__(self, key: object):
        return "{" + str(key) + "}"


@dataclass
class RavenfallFormattedMessage:
    identifier: str | None
    format: str
    format_args: dict[str, object]
    recipient: Recipient
    correlation_id: str | None

    def format_args_as_array(self) -> list[object]:
        """Return format_args values in the order the keys appear in the format string."""
        ordered_values: list[object] = []
        for _, field_name, _, _ in Formatter().parse(self.format):
            if not field_name:
                continue
            ordered_values.append(self.format_args[field_name])
        return ordered_values

    def format_message(self) -> str:
        """Return the formatted string using the format template and format_args.

        If a format argument is missing, leave the placeholder intact (for example
        `{name}`) instead of raising an error.
        """
        return Formatter().vformat(self.format, (), _SafeDict(self.format_args))
