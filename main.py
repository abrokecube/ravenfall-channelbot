import asyncio
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, override

from dotenv import load_dotenv

from bot.cogs.bot import BotStuffCog
from bot.cogs.example import ExampleCog
from bot.cogs.help import HelpCog
from bot.cogs.process_watchdog import ProcessWatchdogCog
from bot.cogs.ravenfall_watcher import RavenfallWatcherCog
from bot.cogs.testing import TestingCog
from bot.core.components import EventManager, GlobalContext
from bot.db.models import update_schema
from bot.db.service import DatabaseService
from bot.integrations.chat_messages import GlobalMessengerService, MessageEvent
from bot.integrations.chat_messages.event_processors import filter_message_event_text
from bot.integrations.commands import CommandDispatcher
from bot.integrations.process_manager import ProcessEventSource
from bot.integrations.ravenfall.event_sources import RavenfallEventSource
from bot.integrations.twitch.dispatchers import TwitchRedeemDispatcher
from bot.integrations.twitch.enums import EventSubTopic, MessageReceiveMode
from bot.integrations.twitch.event_sources import AuthScope, TwitchEventSource
from bot.services.config_service import ConfigModel, ConfigService
from bot.services.event_waiter import EventWaiterService
from bot.services.pastebin_service import PastebinService
from bot.services.prometheus_service import PrometheusService
from bot.services.ravenfall_channels import RavenfallChannelService
from bot.services.ravenfall_multichat import RavenfallMultichatService
from bot.services.remote_bot import RemoteBotService
from bot.services.web_service import WebService
from utils.logging_fomatter import setup_logging

if TYPE_CHECKING:
    from collections.abc import Awaitable

_ = load_dotenv()

with Path("pid").open("w") as f:
    _ = f.write(str(os.getpid()))


class BotConfig(ConfigModel):
    """Top-level bot config."""

    config_table_name: ClassVar[str | None] = "bot"

    log_level: Literal[
        "debug",
        "info",
        "warn",
        "warning",
        "error",
        "critical",
    ] = "info"
    command_prefix: str = "!"
    owner_twitch_id: str


config_service = ConfigService("config.toml")
bot_config = config_service.get_table(BotConfig)

logging_level_strs = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}
logger_config = {
    # "asyncio": {
    #     "console_level": logging.INFO,
    # },
    "twitchAPI": {
        "filename": "twitchAPI.log",
        "console_level": logging.INFO,
    },
    # "middleman": {
    #     "filename": "middleman.log",
    #     "console_level": logging.INFO,
    # },
    "aiosqlite": {
        "filename": "database.log",
        "console_level": logging.INFO,
    },
    "bot.clients.ravenfall_query": {"console_level": logging.CRITICAL},
    # "new_message_processor": {
    #     "filename": "rfmsgproc.log",
    #     "console_level": logging.INFO,
    # },
    # "aiohttp.access": {
    #     "filename": "httpserver.log",
    #     "console_level": logging.WARNING,
    # },
    # "bot.server": {
    #     "filename": "httpserver.log",
    #     "console_level": logging.WARNING,
    # },
    # "utils.runshell": {
    #     "filename": "runshell.log",
    #     "console_level": logging.WARNING,
    # },
    # "bot.ravenfallloc": {
    #     "filename": "rfloc.log",
    #     "console_level": logging.WARNING,
    # },
    # "bot.ravenfallchannel": {
    #     "filename": "rfchannels.log",
    #     "console_level": logging.INFO,
    # },
    # "bot.ravenfallmanager": {
    #     "filename": "rfchannels.log",
    #     "console_level": logging.INFO,
    # },
    # "bot.commands": {
    #     "filename": "commands.log",
    #     "console_level": logging.INFO,
    # },
}

default_console_logging_level = logging_level_strs.get(bot_config.log_level, logging.INFO)
setup_logging(level=default_console_logging_level, loggers_config=logger_config)

logger = logging.getLogger(__name__)


class MyCmdDispatcher(CommandDispatcher):
    """Command dispatcher."""

    def __init__(self):
        super().__init__()

    @override
    async def get_prefix(self, global_context: GlobalContext, event: MessageEvent) -> str:
        return bot_config.command_prefix


async def run():
    """Run."""

    def handle_loop_exception(_: asyncio.AbstractEventLoop, context: dict[str, Any]):  # pyright: ignore [reportExplicitAny]
        logger.exception("Caught async exception: %s", context.get("exception"))

    logger.info("Setting up loop")
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_loop_exception)

    logger.info("Checking db")
    await update_schema()

    global_ctx = GlobalContext()
    event_manager: EventManager | None = None

    try:
        tasks: list[Awaitable[object]] = []
        event_manager = EventManager(global_ctx)
        command_d = MyCmdDispatcher()
        await event_manager.add_dispatcher(command_d)
        await event_manager.add_dispatcher(TwitchRedeemDispatcher())
        event_manager.add_event_processor(MessageEvent, filter_message_event_text)
        twitch = TwitchEventSource(
            [AuthScope.USER_WRITE_CHAT],
            [
                AuthScope.CHAT_READ,
                AuthScope.CHAT_EDIT,
                AuthScope.USER_BOT,
                AuthScope.USER_READ_CHAT,
                AuthScope.USER_WRITE_CHAT,
                AuthScope.MODERATOR_MANAGE_ANNOUNCEMENTS,
            ],
        )
        await global_ctx.register_service(EventWaiterService())
        tasks.append(event_manager.add_event_source(twitch))
        ravenfall_ev_src = RavenfallEventSource()
        tasks.append(event_manager.add_event_source(ravenfall_ev_src))
        tasks.append(event_manager.add_event_source(ProcessEventSource()))

        tasks.append(event_manager.add_cog(TestingCog))
        tasks.append(event_manager.add_cog(HelpCog))
        # tasks.append(event_manager.add_cog(ExampleCog))
        tasks.append(event_manager.add_cog(ProcessWatchdogCog))
        tasks.append(event_manager.add_cog(BotStuffCog))

        tasks.append(global_ctx.register_service(DatabaseService()))
        tasks.append(global_ctx.register_service(RemoteBotService()))
        tasks.append(global_ctx.register_service(config_service))
        tasks.append(global_ctx.register_service(WebService()))
        tasks.append(global_ctx.register_service(PrometheusService()))
        tasks.append(global_ctx.register_service(RavenfallMultichatService()))
        tasks.append(global_ctx.register_service(GlobalMessengerService()))
        tasks.append(global_ctx.register_service(PastebinService()))
        tasks.append(global_ctx.register_service(RavenfallChannelService(event_manager)))

        __ = await asyncio.gather(*tasks)
        tasks.clear()

        twitch_auths: defaultdict[str, set[AuthScope]] = defaultdict(set)

        # twitch_auths[bot_config.owner_twitch_id].update(
        #     [AuthScope.CHANNEL_BOT, AuthScope.CHANNEL_MANAGE_REDEMPTIONS]
        # )

        for instance in ravenfall_ev_src.ravenfall_instances:
            twitch_auths[instance.channel_id].add(AuthScope.CHANNEL_BOT)
            # for linked in instance.config.linked_channels:
            #     if linked.platform == EVENT_SOURCE_TWITCH:
            #         twitch_auths[linked.id].add(AuthScope.CHANNEL_BOT)

        for channel_id, scopes in twitch_auths.items():
            tasks.append(twitch.authenticate_user(channel_id, scopes))

        __ = await asyncio.gather(*tasks)
        tasks.clear()

        for channel_id, scopes in twitch_auths.items():
            if AuthScope.CHANNEL_BOT in scopes:
                tasks.append(
                    twitch.join_chat(
                        channel_id=channel_id, mode=MessageReceiveMode.EVENTSUB
                    )
                )
            if AuthScope.CHANNEL_MANAGE_REDEMPTIONS in scopes:
                tasks.append(
                    twitch.add_eventsub_subscriptions(
                        bot_config.owner_twitch_id,
                        EventSubTopic.CHANNEL_POINTS_CUSTOM_REWARD_REDEMPTION_ADD,
                    )
                )

        __ = await asyncio.gather(*tasks)
        tasks.clear()

        tasks.append(event_manager.add_cog(RavenfallWatcherCog))
        __ = await asyncio.gather(*tasks)
        tasks.clear()

        logger.info("### Bot is ready ###")
        wait_forever = asyncio.Event()
        __ = await wait_forever.wait()
    except asyncio.CancelledError:
        logger.info("Bot is shutting down")
    finally:
        if event_manager:
            await event_manager.teardown()
        await global_ctx.stop_all()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(run())
