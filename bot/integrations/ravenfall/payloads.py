from msgspec import json


def _make_sender_dict(username: str) -> dict[str, object]:
    return {
        "Id": "00000000-0000-0000-0000-000000000000",
        "CharacterId": "00000000-0000-0000-0000-000000000000",
        "Username": username,
        "DisplayName": username,
        "Color": None,
        "Platform": "twitch",
        "PlatformId": "",
        "IsBroadcaster": False,
        "IsModerator": False,
        "IsSubscriber": False,
        "IsVip": False,
        "IsGameAdministrator": False,
        "IsGameModerator": False,
        "SubTier": 0,
        "Identifier": None,
    }


class BaseRavenBotPayload:
    def __init__(self, identifier: str, content: object = None) -> None:
        self.identifier: str = identifier
        if content is None:
            content = {}
        self.content: object = content

    def get_content_json_string(self) -> str:
        """Serialize content to JSON string."""
        return json.encode(self.content).decode("utf-8")


# ── Character ─────────────────────────────────────────────


class JoinGame(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("join")


class LeaveGame(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("leave")


class GetIslandInfo(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("island_info")


class GetFerryInfo(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("ferry_info")


class UseFerryScroll(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("ferry_boost")


class EmbarkFerry(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("ferry_enter")


class SailTo(BaseRavenBotPayload):
    def __init__(self, destination: str) -> None:
        super().__init__("ferry_travel", destination)


class Disembark(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("ferry_leave")


class TeleportTo(BaseRavenBotPayload):
    def __init__(self, destination: str) -> None:
        super().__init__("teleport_island", destination)


class GetWhere(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("island_info")


class JoinOnsen(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("onsen_join")


class LeaveOnsen(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("onsen_leave")


class GetRestedInfo(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("rested_status")


class GetDps(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("dps")


class GetStatusEffects(BaseRavenBotPayload):
    def __init__(self, arguments: str = "") -> None:
        super().__init__("get_status_effects", arguments)


class ObservePlayer(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("observe")


class GetInspect(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("inspect")


class Unstuck(BaseRavenBotPayload):
    def __init__(self, query: str = "") -> None:
        super().__init__("unstuck", query)


class ReloadGame(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("reload")


class UpdateGame(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("update")


class RestartGame(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("restart")


# ── Skills / Tasks ───────────────────────────────────────


class StartPlayerTask(BaseRavenBotPayload):
    def __init__(self, task: str, arguments: list[str] | None = None) -> None:
        super().__init__("task", {"Task": task, "Arguments": arguments or []})


class GetTrainInfo(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("train_info")


class GetStats(BaseRavenBotPayload):
    def __init__(self, query: str = "") -> None:
        super().__init__("player_stats", query)


class GetTrainingInfo(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("train_info")


class Craft(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("craft", item_query)


class Cook(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("cook", item_query)


class Brew(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("brew", item_query)


class Mine(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("mine", item_query)


class Farm(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("farm", item_query)


class Chop(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("chop", item_query)


class Fish(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("fish", item_query)


class Gather(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("gather", item_query)


class Enchant(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("enchant", item_name)


class Disenchant(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("disenchant", item_name)


class GetEnchantCooldown(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("enchantment_cooldown")


class ClearEnchantCooldown(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("clear_enchantment_cooldown")


# ── Combat: Raid ─────────────────────────────────────────


class JoinRaid(BaseRavenBotPayload):
    def __init__(self, query: str = "") -> None:
        super().__init__("raid_join", query)


class AutoJoinRaid(BaseRavenBotPayload):
    def __init__(self, query: str = "") -> None:
        super().__init__("raid_auto", query)


class StartRaid(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("raid_force")


class StopRaid(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("raid_stop")


class KillRaidBoss(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("raid_kill_boss")


class GetRaidSkill(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("raid_skill_get")


class SetRaidSkill(BaseRavenBotPayload):
    def __init__(self, skill: str) -> None:
        super().__init__("raid_skill", skill)


class ClearRaidSkill(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("raid_skill_clear")


class RaidStreamer(BaseRavenBotPayload):
    def __init__(self, target_username: str, *, is_war: bool = False) -> None:
        content = {"Player": _make_sender_dict(target_username), "War": is_war}
        super().__init__("raid_streamer", content)


# ── Combat: Dungeon ──────────────────────────────────────


class JoinDungeon(BaseRavenBotPayload):
    def __init__(self, query: str = "") -> None:
        super().__init__("dungeon_join", query)


class AutoJoinDungeon(BaseRavenBotPayload):
    def __init__(self, query: str = "") -> None:
        super().__init__("dungeon_auto", query)


class StartDungeon(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("dungeon_force")


class StopDungeon(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("dungeon_stop")


class ProceedDungeon(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("dungeon_proceed")


class KillDungeonBoss(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("dungeon_kill_boss")


class GetDungeonSkill(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("dungeon_skill_get")


class SetDungeonSkill(BaseRavenBotPayload):
    def __init__(self, skill: str) -> None:
        super().__init__("dungeon_skill", skill)


class ClearDungeonSkill(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("dungeon_skill_clear")


# ── Auto ──────────────────────────────────────────────────


class AutoRest(BaseRavenBotPayload):
    def __init__(self, start_time: int = 0, end_time: int = 120) -> None:
        super().__init__("auto_rest", {"Values": [start_time, end_time]})


class AutoRestStop(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("auto_rest_stop")


class AutoRestStatus(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("auto_rest_status")


class AutoUse(BaseRavenBotPayload):
    def __init__(self, amount: int) -> None:
        super().__init__("auto_use", amount)


class AutoUseStop(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("auto_use_stop")


class AutoUseStatus(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("auto_use_status")


# ── PvP: Duel ────────────────────────────────────────────


class DuelRequest(BaseRavenBotPayload):
    def __init__(self, target_username: str) -> None:
        super().__init__("duel", _make_sender_dict(target_username))


class CancelDuel(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("duel_cancel")


class AcceptDuel(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("duel_accept")


class DeclineDuel(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("duel_decline")


# ── PvP: Arena ───────────────────────────────────────────


class JoinArena(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("arena_join")


class LeaveArena(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("arena_leave")


class StartArena(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("arena_begin")


class CancelArena(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("arena_end")


class KickFromArena(BaseRavenBotPayload):
    def __init__(self, target_username: str) -> None:
        super().__init__("arena_kick", _make_sender_dict(target_username))


class AddToArena(BaseRavenBotPayload):
    def __init__(self, target_username: str) -> None:
        super().__init__("arena_add", _make_sender_dict(target_username))


# ── Tavern Games ─────────────────────────────────────────


class PlayTicTacToe(BaseRavenBotPayload):
    def __init__(self, position: int) -> None:
        super().__init__("ttt_play", position)


class ActivateTicTacToe(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("ttt_activate")


class ResetTicTacToe(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("ttt_reset")


class PlayPetRace(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("pet_race_play")


class ResetPetRace(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("pet_race_reset")


# ── Items ─────────────────────────────────────────────────


class GetItemCount(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("get_item_count", item_name)


class EquipItem(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("equip", item_name)


class UnequipItem(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("unequip", item_name)


class GetEquipment(BaseRavenBotPayload):
    def __init__(self, target: str = "") -> None:
        super().__init__("player_eq", target)


class GetResources(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("player_resources")


class GetTownResources(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("town_resources")


class GiftItem(BaseRavenBotPayload):
    def __init__(
        self, recipient_user_name: str, item_name: str, item_count: int = 1
    ) -> None:
        super().__init__("gift_item", f"{recipient_user_name} {item_name} {item_count}")


class SendItem(BaseRavenBotPayload):
    def __init__(self, query: str) -> None:
        super().__init__("send_item", query)


class GetItemReqs(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("req_item", item_name)


class GetItemUse(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("item_usage", item_name)


class ExamineItem(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("examine_item", item_name)


class GetItemValue(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("value_item", item_name)


class GetTokenCount(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("token_count")


class GetScrollsCount(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("scrolls_count")


class RedeemTokens(BaseRavenBotPayload):
    def __init__(self, query: str) -> None:
        super().__init__("redeem_tokens", query)


class UseItem(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("use_item", item_query)


class BuyItem(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("buy_item", item_query)


class SellItem(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("sell_item", item_query)


class VendorItem(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("vendor_item", item_query)


class UseMarket(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("marketplace", item_query)


class UseVendor(BaseRavenBotPayload):
    def __init__(self, item_query: str) -> None:
        super().__init__("vendor", item_query)


class GetCoinCount(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("player_resources")


class GetLoot(BaseRavenBotPayload):
    def __init__(self, filter_str: str = "") -> None:
        super().__init__("get_loot", filter_str)


# ── Clan ──────────────────────────────────────────────────


class GetClanInfo(BaseRavenBotPayload):
    def __init__(self, argument: str = "-") -> None:
        super().__init__("clan_info", argument)


class GetClanStats(BaseRavenBotPayload):
    def __init__(self, argument: str = "-") -> None:
        super().__init__("clan_stats", argument)


class GetClanRank(BaseRavenBotPayload):
    def __init__(self, argument: str = "-") -> None:
        super().__init__("clan_rank", argument)


class JoinClan(BaseRavenBotPayload):
    def __init__(self, argument: str) -> None:
        super().__init__("clan_join", argument)


class LeaveClan(BaseRavenBotPayload):
    def __init__(self, argument: str = "-") -> None:
        super().__init__("clan_leave", argument)


class RemoveFromClan(BaseRavenBotPayload):
    def __init__(self, target_username: str) -> None:
        super().__init__("clan_remove", _make_sender_dict(target_username))


class SendClanInvite(BaseRavenBotPayload):
    def __init__(self, target_username: str) -> None:
        super().__init__("clan_invite", _make_sender_dict(target_username))


class AcceptClanInvite(BaseRavenBotPayload):
    def __init__(self, argument: str) -> None:
        super().__init__("clan_accept", argument)


class DeclineClanInvite(BaseRavenBotPayload):
    def __init__(self, argument: str) -> None:
        super().__init__("clan_decline", argument)


class PromoteClanMember(BaseRavenBotPayload):
    def __init__(self, target_username: str, role: str) -> None:
        super().__init__(
            "clan_promote", {"Values": [_make_sender_dict(target_username), role]}
        )


class DemoteClanMember(BaseRavenBotPayload):
    def __init__(self, target_username: str, role: str) -> None:
        super().__init__(
            "clan_demote", {"Values": [_make_sender_dict(target_username), role]}
        )


# ── Appearance ───────────────────────────────────────────


class SetPlayerScale(BaseRavenBotPayload):
    def __init__(self, scale: float) -> None:
        super().__init__("set_player_scale", scale)


class ToggleDiaperMode(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("toggle_diaper_mode")


class ToggleHelmet(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("toggle_helmet")


class TogglePet(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("toggle_pet")


class ToggleItemRequirements(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("toggle_item_requirements")


class TurnIntoMonster(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("monster")


class GetPet(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("get_pet")


class SetPet(BaseRavenBotPayload):
    def __init__(self, item_name: str) -> None:
        super().__init__("set_pet", item_name)


class ChangeAppearance(BaseRavenBotPayload):
    def __init__(self, appearance_code: str) -> None:
        super().__init__("change_appearance", appearance_code)


# ── Admin / Game ─────────────────────────────────────────


class KickPlayer(BaseRavenBotPayload):
    def __init__(self, target_username: str) -> None:
        super().__init__("kick", _make_sender_dict(target_username))


class GetClientVersion(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("client_version")


class GetPlayerCount(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("player_count")


class GetMultiplierInfo(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("multiplier")


class SetTimeOfDay(BaseRavenBotPayload):
    def __init__(self, total_time: int, freeze_time: int) -> None:
        super().__init__("set_time", {"TotalTime": total_time, "FreezeTime": freeze_time})


class UseExpScroll(BaseRavenBotPayload):
    def __init__(self, amount: int = 1) -> None:
        super().__init__("use_exp_scroll", amount)


class SetExpMultiplier(BaseRavenBotPayload):
    def __init__(self, value: int) -> None:
        super().__init__("exp_multiplier", value)


class SetExpMultiplierLimit(BaseRavenBotPayload):
    def __init__(self, value: int) -> None:
        super().__init__("exp_multiplier_limit", value)


class ItemDropEvent(BaseRavenBotPayload):
    def __init__(self, item: str) -> None:
        super().__init__("item_drop_event", item)


class GetHighestSkill(BaseRavenBotPayload):
    def __init__(self, skill: str = "") -> None:
        super().__init__("highest_skill", skill)


class GetSkillHighscore(BaseRavenBotPayload):
    def __init__(self, skill: str = "") -> None:
        super().__init__("highscore", skill)


class SetVillageHuts(BaseRavenBotPayload):
    def __init__(self, skill: str = "") -> None:
        super().__init__("set_village_huts", skill)


class GetVillageBoost(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("get_village_boost")


class GetVillageStats(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("village_stats")


class GetVillagers(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("villagers")


class UploadState(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("upload_state")


class UploadLog(BaseRavenBotPayload):
    def __init__(self) -> None:
        super().__init__("upload_log")


class SendChatMessage(BaseRavenBotPayload):
    def __init__(self, message: str) -> None:
        super().__init__("chat_message", message)


# ── Renamed aliases for backwards compat ─────────────────


class Train(BaseRavenBotPayload):
    def __init__(self, skill: str) -> None:
        skill = skill.lower()
        if skill == "alchemy":
            skill = "brewing"
        match skill:
            case "sailing":
                super().__init__("ferry_enter")
            case (
                "attack"
                | "defense"
                | "strength"
                | "all"
                | "magic"
                | "ranged"
                | "healing"
                | "health"
            ):
                super().__init__("task", {"Task": "Fighting", "Arguments": [skill]})
            case (
                "woodcutting"
                | "fishing"
                | "mining"
                | "crafting"
                | "cooking"
                | "farming"
                | "gathering"
                | "brewing"
            ):
                super().__init__("task", {"Task": skill.capitalize(), "Arguments": []})
            case _:
                raise ValueError("Invalid skill")


class Sail(BaseRavenBotPayload):
    def __init__(self, destination: str | None = None) -> None:
        if destination:
            if destination.lower() not in {
                "home",
                "away",
                "ironhill",
                "kyo",
                "heim",
                "atria",
                "eldara",
            }:
                msg = f"Invalid island '{destination}'."
                raise ValueError(msg)
            super().__init__("ferry_travel", destination)
        else:
            super().__init__("ferry_enter")
