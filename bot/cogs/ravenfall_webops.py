import asyncio
import logging
import random
from typing import ClassVar, override

from bot.clients.rf_webops_client import Character, WebOpsClient
from bot.core.components import Cog, EventManager
from bot.integrations.chat_messages import UserRole
from bot.integrations.chat_messages.deco import checks
from bot.integrations.chat_messages.utils import min_permission_level
from bot.integrations.commands import (
    Choice,
    CommandError,
    CommandEvent,
    MinPermissionLevel,
    command,
    parameter,
)
from bot.integrations.ravenfall import (
    RavenfallInstance,
    RavenfallInstanceConverter,
    RavenfallService,
)
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService
from bot.services.pastebin_service import PastebinService
from bot.services.ravenfall_multichat import RavenfallMultichatService
from utils.strings import EN_DASH

LOGGER = logging.getLogger(__name__)


class RavenfallWebOpsConfig(ConfigModel):
    config_table_name: ClassVar[str | None] = "cogs.ravenfall_webops"
    base_url: str = "http://127.0.0.1:7102"


class RavenfallWebOpsCog(Cog, ConfigSubscriberMixin):
    """Ravenfall web automation."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self.client: WebOpsClient = WebOpsClient()
        self.lock: asyncio.Lock = asyncio.Lock()

    @override
    async def setup(self) -> None:
        config_srv = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_srv)
        config = self.subscribe_config(RavenfallWebOpsConfig)
        self.client = WebOpsClient(config.base_url)

    @override
    async def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if not isinstance(config, RavenfallWebOpsConfig):
            return
        self.client = WebOpsClient(config.base_url)

    def _check_permission(
        self,
        ctx: CommandEvent,
        instance: RavenfallInstance,
        min_role: UserRole = UserRole.BOT_ADMINISTRATOR,
    ):
        if instance.channel_id != ctx.message.room_id and not min_permission_level(
            ctx.message, min_role
        ):
            msg = "You do not have permission to specify an instance."
            raise CommandError(msg)

    def _check_online(self):
        ravenfall_srv = self.g_ctx.require_service(RavenfallService)
        if not ravenfall_srv.ravennest_is_online.is_set():
            raise CommandError("RavenNest is offline.")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter(
        "item",
        converter=Choice(
            {
                "raid": ["raid", "raid scroll", "r"],
                "dungeon": ["dungeon", "dungeon scroll", "d"],
                "exp": ["exp", "exp multiplier scroll", "e"],
            },
            title="Scroll",
        ),
        regex=r"^[a-zA-Z ]+$",
    )
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command()
    async def restockscrolls(
        self,
        ctx: CommandEvent,
        item: str,
        count: int,
        *,
        instance: RavenfallInstance,
    ):
        """Restocks scrolls in the loyalty shop."""
        self._check_permission(ctx, instance)
        self._check_online()

        if self.lock.locked():
            raise CommandError(
                "There is currently an ongoing operation. Try again later."
            )
        async with self.lock:
            use_all_users = False
            if item == "raid":
                item_name = "Raid Scroll"
                item_id = "raid_scroll"
            elif item == "dungeon":
                item_name = "Dungeon Scroll"
                item_id = "dungeon_scroll"
            elif item == "exp":
                item_name = "Exp Multiplier Scroll"
                item_id = "exp_multiplier_scroll"
                use_all_users = True
            else:
                raise CommandError("Invalid item.")

            multichat = self.g_ctx.require_service(RavenfallMultichatService).get_client()
            try:
                chars = await multichat.get_char_info()
            except Exception as e:
                LOGGER.exception("failed to get char info")
                raise CommandError("Failed to get character info.") from e

            char_list: list[Character] = []
            users_used: set[str] = set()

            for char in chars:
                if (
                    char.channel_id == instance.channel_id
                    and char.user_name not in users_used
                ):
                    char_list.append({"username": char.user_name, "id": str(char.index)})
                    users_used.add(char.user_name)
            if use_all_users:
                for char in chars:
                    if char.user_name not in users_used:
                        char_list.append(
                            {"username": char.user_name, "id": str(char.index)}
                        )
                        users_used.add(char.user_name)

            await ctx.reply(f"Restocking {count}x {item_name}, please wait...")
            random.shuffle(char_list)

            try:
                result = await self.client.redeem_items(item_id, count, char_list)
            except TimeoutError as e:
                raise CommandError("Task timed out.") from e
            except Exception as e:
                LOGGER.exception("Failed to restock scrolls")
                raise CommandError("Task failed.") from e
            else:
                sum_scrolls = sum(result.redeemed.values())
                await ctx.reply(f"Successfully restocked {sum_scrolls}x {item_name}.")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command()
    async def countloyaltypoints(
        self,
        ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Gets the total loyalty points across characters in a channel.
        Will take a few minutes to count.
        """
        pastebin_srv = self.global_context.require_service(PastebinService)
        self._check_permission(ctx, instance)
        self._check_online()

        if self.lock.locked():
            raise CommandError(
                "There is currently an ongoing operation. Try again later."
            )
        async with self.lock:
            multichat = self.g_ctx.require_service(RavenfallMultichatService).get_client()
            try:
                chars = await multichat.get_char_info()
            except Exception as e:
                LOGGER.exception("failed to get char info")
                raise CommandError("Failed to get character info.") from e
            char_list = set[str]()
            channel_char_list = set[str]()
            for char in chars:
                if char.channel_id == instance.channel_id:
                    channel_char_list.add(char.user_name)
                char_list.add(char.user_name)
            await ctx.reply("Counting loyalty points, please wait...")
            try:
                result = await self.client.get_total_loyalty_points(tuple(char_list))
            except TimeoutError as e:
                raise CommandError("Task timed out.") from e
            except Exception as e:
                LOGGER.exception("Failed to fetch data.")
                raise CommandError("Failed to fetch data.") from e

        out_str: list[str] = []

        out_str.append(f"Loyalty points info for {instance.channel_name}")
        out_str.append("")
        points_in_channel = 0
        total_points = 0
        for char_name in sorted(channel_char_list):
            points = result.breakdown.get(char_name, 0)
            if points == -1:
                out_str.append(f"{char_name}: Failed to get points")
                continue
            points_in_channel += points
            total_points += points
            out_str.append(f"{char_name}: {points:,} points")
        out_str.append("")
        out_str.append("Characters not in this channel:")
        for char_name in sorted(char_list - channel_char_list):
            points = result.breakdown.get(char_name, 0)
            if points == -1:
                out_str.append(f"{char_name}: Failed to get points")
                continue
            total_points += points
            out_str.append(f"{char_name}: {points:,} points")
        out_str.append("")
        out_str.append(f"Points in {instance.channel_name}: {points_in_channel:,} points")
        out_str.append(f"Total: {total_points:,} points")
        out_str.append("")
        upload_result = await pastebin_srv.upload_text("\n".join(out_str))
        await ctx.message.reply(
            f"In this channel: {points_in_channel:,} points {EN_DASH} "
            f"Total: {result.total_points:,} points {EN_DASH} "
            f"Breakdown: {upload_result.url}"
        )
