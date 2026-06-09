from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .payloads import BaseRavenBotPayload

from .payloads import (
    AcceptClanInvite,
    AcceptDuel,
    ActivateTicTacToe,
    AddToArena,
    AutoJoinDungeon,
    AutoJoinRaid,
    AutoRest,
    AutoRestStatus,
    AutoRestStop,
    AutoUse,
    AutoUseStatus,
    AutoUseStop,
    Brew,
    BuyItem,
    CancelArena,
    CancelDuel,
    ChangeAppearance,
    Chop,
    ClearDungeonSkill,
    ClearRaidSkill,
    Cook,
    Craft,
    DeclineClanInvite,
    DeclineDuel,
    DemoteClanMember,
    Disembark,
    Disenchant,
    DuelRequest,
    Enchant,
    EquipItem,
    ExamineItem,
    Farm,
    Fish,
    Gather,
    GetClanInfo,
    GetClanRank,
    GetClanStats,
    GetClientVersion,
    GetDps,
    GetDungeonSkill,
    GetEquipment,
    GetFerryInfo,
    GetHighestSkill,
    GetItemCount,
    GetItemReqs,
    GetItemUse,
    GetItemValue,
    GetLoot,
    GetMultiplierInfo,
    GetPet,
    GetPlayerCount,
    GetRaidSkill,
    GetResources,
    GetRestedInfo,
    GetScrollsCount,
    GetSkillHighscore,
    GetStats,
    GetStatusEffects,
    GetTokenCount,
    GetTownResources,
    GetTrainingInfo,
    GetVillageBoost,
    GetVillagers,
    GetWhere,
    GiftItem,
    ItemDropEvent,
    JoinArena,
    JoinClan,
    JoinDungeon,
    JoinGame,
    JoinOnsen,
    JoinRaid,
    KickFromArena,
    KickPlayer,
    KillDungeonBoss,
    KillRaidBoss,
    LeaveArena,
    LeaveClan,
    LeaveGame,
    LeaveOnsen,
    Mine,
    ObservePlayer,
    PlayPetRace,
    ProceedDungeon,
    PromoteClanMember,
    RaidStreamer,
    RedeemTokens,
    ReloadGame,
    RemoveFromClan,
    ResetPetRace,
    ResetTicTacToe,
    RestartGame,
    Sail,
    SailTo,
    SellItem,
    SendClanInvite,
    SendItem,
    SetDungeonSkill,
    SetExpMultiplier,
    SetExpMultiplierLimit,
    SetPet,
    SetPlayerScale,
    SetRaidSkill,
    SetTimeOfDay,
    StartArena,
    StartDungeon,
    StartRaid,
    StopDungeon,
    StopRaid,
    TeleportTo,
    ToggleDiaperMode,
    ToggleHelmet,
    ToggleItemRequirements,
    TogglePet,
    Train,
    TurnIntoMonster,
    UnequipItem,
    Unstuck,
    UpdateGame,
    UploadLog,
    UploadState,
    UseExpScroll,
    UseItem,
    UseMarket,
    VendorItem,
)


@dataclass
class CommandDef:
    payload_cls: type[BaseRavenBotPayload] | None = None
    payload_factory: Callable[[str], BaseRavenBotPayload] | None = None
    min_permission: str = "everyone"
    help_text: str = ""
    aliases: list[str] = field(default_factory=list)
    requires_arg: bool = False
    no_arg_command: CommandDef | None = None

    def build(self, args: str) -> BaseRavenBotPayload:
        """Build a payload from parsed arguments."""
        if self.payload_factory:
            return self.payload_factory(args)
        if self.payload_cls:
            if self.requires_arg:
                return self.payload_cls(args)
            return self.payload_cls()  # pyright: ignore[reportCallIssue]
        msg = "Command has no payload"
        raise RuntimeError(msg)


_COMMANDS: dict[str, CommandDef] = {}
_ALIASES: dict[str, str] = {}


def _cmd(
    name: str,
    payload_cls: type[BaseRavenBotPayload] | None = None,
    *,
    payload_factory: Callable[[str], BaseRavenBotPayload] | None = None,
    aliases: list[str] | None = None,
    min_permission: str = "everyone",
    help_text: str = "",
    requires_arg: bool = False,
    no_arg_command: CommandDef | None = None,
) -> CommandDef:
    defn = CommandDef(
        payload_cls=payload_cls,
        payload_factory=payload_factory,
        min_permission=min_permission,
        help_text=help_text,
        aliases=aliases or [],
        requires_arg=requires_arg,
        no_arg_command=no_arg_command,
    )
    _COMMANDS[name] = defn
    for alias in aliases or []:
        _ALIASES[alias] = name
    return defn


# ── Character ─────────────────────────────────────────────

_ = _cmd("join", payload_cls=JoinGame, help_text="Join the game.")
_ = _cmd("leave", payload_cls=LeaveGame, help_text="Leave the game.")
_ = _cmd(
    "sail",
    payload_cls=Sail,
    aliases=["travel"],
    help_text="Travel to an island or board the ferry.",
)
_ = _cmd(
    "sail to",
    payload_cls=SailTo,
    requires_arg=True,
    help_text="Sail to a specific island.",
)
_ = _cmd(
    "teleport",
    payload_cls=TeleportTo,
    requires_arg=True,
    help_text="Teleport to an island.",
)
_ = _cmd("disembark", payload_cls=Disembark, help_text="Leave the ferry.")
_ = _cmd(
    "where",
    payload_cls=GetWhere,
    aliases=["island"],
    help_text="Check your current location.",
)
_ = _cmd("rest", payload_cls=JoinOnsen, aliases=["onsen"], help_text="Rest at the onsen.")
_ = _cmd("unrest", payload_cls=LeaveOnsen, help_text="Leave the onsen.")
_ = _cmd("rested", payload_cls=GetRestedInfo, help_text="Check rested status.")
_ = _cmd(
    "damage", payload_cls=GetDps, aliases=["dps", "dmg"], help_text="Check your DPS info."
)
_ = _cmd(
    "status",
    payload_cls=GetStatusEffects,
    aliases=["effects"],
    help_text="Check your status effects.",
)
_ = _cmd(
    "show",
    payload_cls=ObservePlayer,
    aliases=["observe"],
    help_text="Observe another player.",
)
_ = _cmd("ferry", payload_cls=GetFerryInfo, help_text="Check ferry status.")

# ── Skills ────────────────────────────────────────────────

_ = _cmd(
    "train", payload_cls=Train, requires_arg=True, help_text="Start training a skill."
)
_ = _cmd(
    "training", payload_cls=GetTrainingInfo, help_text="Check your current training."
)
_ = _cmd(
    "inspect",
    payload_cls=ObservePlayer,
    requires_arg=True,
    help_text="Inspect another player.",
)
_ = _cmd("stats", payload_cls=GetStats, aliases=["stat"], help_text="View your stats.")
_ = _cmd(
    "craft", payload_cls=Craft, aliases=["create", "make"], help_text="Craft an item."
)
_ = _cmd("cook", payload_cls=Cook, aliases=["prepare"], help_text="Cook an item.")
_ = _cmd(
    "brew",
    payload_cls=Brew,
    aliases=["conjure", "alchemise", "alchemize"],
    help_text="Brew a potion.",
)
_ = _cmd("mine", payload_cls=Mine, requires_arg=True, help_text="Mine an item.")
_ = _cmd("farm", payload_cls=Farm, requires_arg=True, help_text="Farm an item.")
_ = _cmd("chop", payload_cls=Chop, requires_arg=True, help_text="Chop wood.")
_ = _cmd("fish", payload_cls=Fish, requires_arg=True, help_text="Fish for an item.")
_ = _cmd("gather", payload_cls=Gather, requires_arg=True, help_text="Gather an item.")
_ = _cmd("enchant", payload_cls=Enchant, requires_arg=True, help_text="Enchant an item.")
_ = _cmd(
    "disenchant",
    payload_cls=Disenchant,
    requires_arg=True,
    help_text="Disenchant an item.",
)

# ── Combat: Raid ──────────────────────────────────────────

_ = _cmd("raid", payload_cls=JoinRaid, help_text="Join an active raid or manage raids.")
_ = _cmd(
    "raid start",
    payload_cls=StartRaid,
    min_permission="moderator",
    help_text="Force start a raid.",
)
_ = _cmd("raid auto", payload_cls=AutoJoinRaid, help_text="Toggle auto-join raid.")
_ = _cmd(
    "raid kill boss",
    payload_cls=KillRaidBoss,
    min_permission="moderator",
    help_text="Kill the current raid boss.",
)
_ = _cmd(
    "raid stop",
    payload_cls=StopRaid,
    min_permission="moderator",
    help_text="Stop the active raid.",
)
_ = _cmd(
    "raid war",
    payload_cls=RaidStreamer,
    requires_arg=True,
    min_permission="broadcaster",
    help_text="Raid war another streamer.",
)
_ = _cmd(
    "raid skill",
    payload_cls=GetRaidSkill,
    no_arg_command=CommandDef(
        payload_cls=SetRaidSkill,
        requires_arg=True,
        help_text="Set your raid combat style.",
    ),
    help_text="Show your raid combat style.",
)
_ = _cmd(
    "raid skill clear",
    payload_cls=ClearRaidSkill,
    aliases=["raid style clear"],
    help_text="Clear your raid combat style.",
)

# ── Combat: Dungeon ───────────────────────────────────────

_ = _cmd(
    "dungeon",
    payload_cls=JoinDungeon,
    help_text="Join an active dungeon or manage dungeons.",
)
_ = _cmd(
    "dungeon start",
    payload_cls=StartDungeon,
    min_permission="moderator",
    help_text="Force start a dungeon.",
)
_ = _cmd(
    "dungeon auto", payload_cls=AutoJoinDungeon, help_text="Toggle auto-join dungeon."
)
_ = _cmd(
    "dungeon proceed",
    payload_cls=ProceedDungeon,
    min_permission="moderator",
    aliases=["dungeon next room"],
    help_text="Proceed to the next room.",
)
_ = _cmd(
    "dungeon kill boss",
    payload_cls=KillDungeonBoss,
    min_permission="moderator",
    help_text="Kill the current dungeon boss.",
)
_ = _cmd(
    "dungeon stop",
    payload_cls=StopDungeon,
    min_permission="moderator",
    help_text="Stop the active dungeon.",
)
_ = _cmd(
    "dungeon skill",
    payload_cls=GetDungeonSkill,
    no_arg_command=CommandDef(
        payload_cls=SetDungeonSkill,
        requires_arg=True,
        help_text="Set your dungeon combat style.",
    ),
    help_text="Show your dungeon combat style.",
)
_ = _cmd(
    "dungeon skill clear",
    payload_cls=ClearDungeonSkill,
    aliases=["dungeon style clear"],
    help_text="Clear your dungeon combat style.",
)


def _parse_auto_rest(args: str) -> AutoRest:
    parts = args.split()
    start = 0
    end = 120
    if parts and parts[-1].isdigit():
        end = int(parts.pop())
    if parts and parts[-1].isdigit():
        start = int(parts.pop())
    return AutoRest(start, end)


# ── Auto ──────────────────────────────────────────────────

_ = _cmd(
    "auto dungeon", payload_cls=AutoJoinDungeon, help_text="Toggle auto-join dungeon."
)
_ = _cmd("auto raid", payload_cls=AutoJoinRaid, help_text="Toggle auto-join raid.")
_ = _cmd("auto rest", payload_factory=_parse_auto_rest, help_text="Set auto-rest timer.")
_ = _cmd(
    "auto rest stop",
    payload_cls=AutoRestStop,
    aliases=["auto rest off", "auto rest clear"],
    help_text="Stop auto-rest.",
)
_ = _cmd(
    "auto rest status", payload_cls=AutoRestStatus, help_text="Check auto-rest status."
)
_ = _cmd("auto use", payload_cls=AutoUse, requires_arg=True, help_text="Auto-use items.")
_ = _cmd(
    "auto use stop",
    payload_cls=AutoUseStop,
    aliases=["auto use off", "auto use clear"],
    help_text="Stop auto-use.",
)
_ = _cmd("auto use status", payload_cls=AutoUseStatus, help_text="Check auto-use status.")


# ── Loot ──────────────────────────────────────────────────

_ = _cmd("loot", payload_cls=GetLoot, help_text="Check your loot.")

# ── Items ─────────────────────────────────────────────────

_ = _cmd(
    "items",
    payload_cls=GetItemCount,
    aliases=["count"],
    requires_arg=True,
    help_text="Check your item count.",
)
_ = _cmd("equip", payload_cls=EquipItem, requires_arg=True, help_text="Equip an item.")
_ = _cmd(
    "unequip", payload_cls=UnequipItem, requires_arg=True, help_text="Unequip an item."
)
_ = _cmd(
    "equipment",
    payload_cls=GetEquipment,
    aliases=["eq"],
    help_text="Check your equipment.",
)
_ = _cmd(
    "resources",
    payload_cls=GetResources,
    aliases=["res", "coins"],
    help_text="Check your resources and coins.",
)
_ = _cmd(
    "townresources",
    payload_cls=GetTownResources,
    aliases=["townres"],
    help_text="Check town resources.",
)
_ = _cmd(
    "gift",
    payload_cls=GiftItem,
    requires_arg=True,
    help_text="Gift an item to another player.",
)
_ = _cmd(
    "send",
    payload_cls=SendItem,
    requires_arg=True,
    help_text="Send items to another character.",
)
_ = _cmd(
    "requirement",
    payload_cls=GetItemReqs,
    aliases=["req"],
    requires_arg=True,
    help_text="Check item crafting requirements.",
)
_ = _cmd(
    "usage",
    payload_cls=GetItemUse,
    aliases=["uses"],
    requires_arg=True,
    help_text="Check what an item is used for.",
)
_ = _cmd(
    "examine",
    payload_cls=ExamineItem,
    aliases=["description"],
    requires_arg=True,
    help_text="Examine an item's description.",
)
_ = _cmd(
    "value",
    payload_cls=GetItemValue,
    aliases=["val"],
    requires_arg=True,
    help_text="Check an item's value.",
)
_ = _cmd("scrolls", payload_cls=GetScrollsCount, help_text="Check your scroll count.")
_ = _cmd(
    "tokens",
    payload_cls=GetTokenCount,
    aliases=["token"],
    help_text="Check your token count.",
)
_ = _cmd(
    "redeem",
    payload_cls=RedeemTokens,
    aliases=["claim"],
    requires_arg=True,
    help_text="Redeem tokens.",
)
_ = _cmd(
    "use",
    payload_cls=UseItem,
    aliases=["eat", "drink", "consume"],
    requires_arg=True,
    help_text="Use an item.",
)
_ = _cmd(
    "buy",
    payload_cls=BuyItem,
    requires_arg=True,
    help_text="Buy an item from the marketplace.",
)
_ = _cmd(
    "sell",
    payload_cls=SellItem,
    requires_arg=True,
    help_text="Sell an item on the marketplace.",
)
_ = _cmd(
    "vendor",
    payload_cls=VendorItem,
    aliases=["vend"],
    requires_arg=True,
    help_text="Sell an item to a vendor.",
)
_ = _cmd(
    "market",
    payload_cls=UseMarket,
    requires_arg=True,
    help_text="Interact with the marketplace.",
)

# ── Clan ──────────────────────────────────────────────────

_ = _cmd("clan", payload_cls=GetClanInfo, help_text="Clan management.")
_ = _cmd("clan info", payload_cls=GetClanInfo, help_text="View clan info.")
_ = _cmd("clan stats", payload_cls=GetClanStats, help_text="View clan stats.")
_ = _cmd(
    "clan rank",
    payload_cls=GetClanRank,
    aliases=["clan role"],
    help_text="View your clan rank.",
)
_ = _cmd("clan join", payload_cls=JoinClan, requires_arg=True, help_text="Join a clan.")
_ = _cmd("clan leave", payload_cls=LeaveClan, help_text="Leave your clan.")
_ = _cmd(
    "clan invite",
    payload_cls=SendClanInvite,
    requires_arg=True,
    min_permission="moderator",
    help_text="Invite a player to the clan.",
)
_ = _cmd(
    "clan accept",
    payload_cls=AcceptClanInvite,
    requires_arg=True,
    help_text="Accept a clan invite.",
)
_ = _cmd(
    "clan decline",
    payload_cls=DeclineClanInvite,
    requires_arg=True,
    help_text="Decline a clan invite.",
)
_ = _cmd(
    "clan remove",
    payload_cls=RemoveFromClan,
    aliases=["clan kick"],
    requires_arg=True,
    min_permission="moderator",
    help_text="Remove a player from the clan.",
)
_ = _cmd(
    "clan promote",
    payload_cls=PromoteClanMember,
    requires_arg=True,
    min_permission="moderator",
    help_text="Promote a clan member.",
)
_ = _cmd(
    "clan demote",
    payload_cls=DemoteClanMember,
    requires_arg=True,
    min_permission="moderator",
    help_text="Demote a clan member.",
)

# ── Appearance ────────────────────────────────────────────

_ = _cmd(
    "big",
    payload_cls=SetPlayerScale,
    payload_factory=lambda _: SetPlayerScale(3.0),
    help_text="Make yourself big.",
)
_ = _cmd(
    "small",
    payload_cls=SetPlayerScale,
    payload_factory=lambda _: SetPlayerScale(0.25),
    help_text="Make yourself small.",
)
_ = _cmd("diaper", payload_cls=ToggleDiaperMode, help_text="Toggle diaper mode.")
_ = _cmd("monster", payload_cls=TurnIntoMonster, help_text="Turn into a monster.")
_ = _cmd("toggle", payload_cls=ToggleHelmet, help_text="Toggle helmet or pet visibility.")
_ = _cmd("toggle helmet", payload_cls=ToggleHelmet, help_text="Toggle helmet visibility.")
_ = _cmd("toggle pet", payload_cls=TogglePet, help_text="Toggle pet visibility.")
_ = _cmd(
    "toggle requirements",
    payload_cls=ToggleItemRequirements,
    help_text="Toggle item requirement display.",
)
_ = _cmd(
    "pet",
    payload_cls=GetPet,
    no_arg_command=CommandDef(
        payload_cls=SetPet, requires_arg=True, help_text="Set your pet."
    ),
    help_text="Show your pet.",
)
_ = _cmd(
    "appearance",
    payload_cls=ChangeAppearance,
    requires_arg=True,
    help_text="Change your character appearance.",
)

# ── PvP ───────────────────────────────────────────────────

_ = _cmd("duel", payload_cls=DuelRequest, requires_arg=True, help_text="Request a duel.")
_ = _cmd("duel accept", payload_cls=AcceptDuel, help_text="Accept a duel request.")
_ = _cmd("duel decline", payload_cls=DeclineDuel, help_text="Decline a duel request.")
_ = _cmd("duel cancel", payload_cls=CancelDuel, help_text="Cancel your duel request.")
_ = _cmd("arena", payload_cls=JoinArena, help_text="Arena commands.")
_ = _cmd("arena join", payload_cls=JoinArena, help_text="Join the arena.")
_ = _cmd("arena leave", payload_cls=LeaveArena, help_text="Leave the arena.")
_ = _cmd(
    "arena start",
    payload_cls=StartArena,
    aliases=["arena begin"],
    help_text="Start the arena.",
)
_ = _cmd(
    "arena cancel",
    payload_cls=CancelArena,
    aliases=["arena end"],
    min_permission="moderator",
    help_text="Cancel the arena.",
)
_ = _cmd(
    "arena kick",
    payload_cls=KickFromArena,
    requires_arg=True,
    min_permission="moderator",
    help_text="Kick a player from the arena.",
)
_ = _cmd(
    "arena add",
    payload_cls=AddToArena,
    requires_arg=True,
    min_permission="moderator",
    help_text="Add a player to the arena.",
)

# ── Tavern Games ──────────────────────────────────────────

_ = _cmd(
    "tictactoe",
    payload_cls=ActivateTicTacToe,
    aliases=["ttt"],
    help_text="Play tic-tac-toe.",
)
_ = _cmd(
    "tictactoe reset",
    payload_cls=ResetTicTacToe,
    aliases=["ttt reset"],
    help_text="Reset the tic-tac-toe board.",
)
_ = _cmd("race", payload_cls=PlayPetRace, help_text="Start a pet race.")
_ = _cmd("race reset", payload_cls=ResetPetRace, help_text="Reset the pet race.")

# ── Admin / Game ──────────────────────────────────────────

_ = _cmd(
    "unstuck",
    payload_cls=Unstuck,
    aliases=["stuck"],
    help_text="Unstuck yourself or others.",
)
_ = _cmd(
    "kick",
    payload_cls=KickPlayer,
    requires_arg=True,
    min_permission="moderator",
    help_text="Kick a player from the game.",
)
_ = _cmd(
    "day",
    payload_factory=lambda _: SetTimeOfDay(0, 15),
    min_permission="broadcaster",
    help_text="Set the in-game time to day.",
)
_ = _cmd(
    "night",
    payload_factory=lambda _: SetTimeOfDay(230, 30),
    min_permission="broadcaster",
    help_text="Set the in-game time to night.",
)
_ = _cmd(
    "online",
    payload_cls=GetPlayerCount,
    aliases=["players", "playercount"],
    help_text="Check how many players are online.",
)
_ = _cmd(
    "exp",
    payload_cls=UseExpScroll,
    payload_factory=lambda args: UseExpScroll(
        int(args.strip()) if args.strip().isdigit() else 1
    ),
    help_text="Use experience multiplier scrolls.",
)
_ = _cmd(
    "drop",
    payload_cls=ItemDropEvent,
    requires_arg=True,
    min_permission="broadcaster",
    help_text="Trigger an item drop event.",
)
_ = _cmd(
    "version",
    payload_cls=GetClientVersion,
    aliases=["ver"],
    help_text="Check the client version.",
)
_ = _cmd(
    "multiplier",
    payload_cls=GetMultiplierInfo,
    aliases=["mult"],
    help_text="Check the current EXP multiplier.",
)
_ = _cmd(
    "highest",
    payload_cls=GetHighestSkill,
    aliases=["top"],
    help_text="Show your highest skill.",
)
_ = _cmd(
    "highscore",
    payload_cls=GetSkillHighscore,
    aliases=["hs", "leaderboard"],
    help_text="Check the skill leaderboard.",
)
_ = _cmd(
    "town",
    payload_cls=GetVillageBoost,
    aliases=["village"],
    help_text="View village info or set village huts.",
)
_ = _cmd(
    "town res",
    payload_cls=GetTownResources,
    aliases=["town resources", "townres", "village resources"],
    help_text="Check town resources.",
)
_ = _cmd(
    "villagers",
    payload_cls=GetVillagers,
    aliases=["huts"],
    help_text="Check your villagers.",
)
_ = _cmd("upload", payload_cls=UploadState, help_text="Upload game state.")
_ = _cmd("upload log", payload_cls=UploadLog, help_text="Upload game log.")
_ = _cmd(
    "restart",
    payload_cls=RestartGame,
    min_permission="broadcaster",
    help_text="Restart the game client.",
)
_ = _cmd(
    "setexp",
    payload_cls=SetExpMultiplier,
    requires_arg=True,
    min_permission="broadcaster",
    help_text="Set the EXP multiplier value.",
)
_ = _cmd(
    "setexplimit",
    payload_cls=SetExpMultiplierLimit,
    requires_arg=True,
    min_permission="broadcaster",
    help_text="Set the EXP multiplier limit.",
)
_ = _cmd(
    "reload",
    payload_cls=ReloadGame,
    min_permission="broadcaster",
    aliases=["ravenfall reload"],
    help_text="Reload the game.",
)
_ = _cmd(
    "update",
    payload_cls=UpdateGame,
    min_permission="broadcaster",
    aliases=["ravenfall update"],
    help_text="Update the game.",
)

# ── Public registry access ────────────────────────────────

COMMANDS: dict[str, CommandDef] = _COMMANDS
ALIASES: dict[str, str] = _ALIASES
