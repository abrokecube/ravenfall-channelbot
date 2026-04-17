import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from dotenv import load_dotenv

from bot.cogs.example import ExampleCog
from bot.cogs.help import HelpCog
from bot.cogs.prometheus_test import PrometheusTestCog
from bot.cogs.testing import TestingCog
from bot.core.components import EventManager, GlobalContext
from bot.db.models import update_schema
from bot.db.service import DatabaseService
from bot.integrations.chat_messages.event_processors import filter_message_event_text
from bot.integrations.chat_messages.events import MessageEvent
from bot.integrations.commands.dispatchers import CommandDispatcher
from bot.integrations.ravenfall import RavenfallConfig
from bot.integrations.ravenfall.event_sources import RavenfallEventSource
from bot.integrations.twitch.dispatchers import TwitchRedeemDispatcher
from bot.integrations.twitch.event_sources import AuthScope, TwitchEventSource
from bot.services.config_service import ConfigService
from bot.services.prometheus_service import PrometheusService
from bot.services.remote_bot_service import RemoteBotService
from bot.services.web_service import WebService
from utils.logging_fomatter import setup_logging

if TYPE_CHECKING:
    from collections.abc import Awaitable

_ = load_dotenv()

with Path("pid").open("w") as f:
    _ = f.write(str(os.getpid()))

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

log_level = os.getenv("LOG_LEVEL", "info")
default_console_logging_level = logging_level_strs.get(log_level.lower(), logging.INFO)
setup_logging(level=default_console_logging_level, loggers_config=logger_config)
# setup_logging(level=default_console_logging_level)

logger = logging.getLogger(__name__)
if log_level.lower() not in logging_level_strs:
    logger.warning(f"Invalid logging level '{log_level}'")


class MyCmdDispatcher(CommandDispatcher):
    """Command dispatcher."""

    def __init__(self):
        super().__init__()

    @override
    async def get_prefix(self, global_context: GlobalContext, event: MessageEvent) -> str:
        return os.getenv("BOT_COMMAND_PREFIX", "!")


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

    tasks: list[Awaitable[None]] = []
    event_manager = EventManager(global_ctx)
    command_d = MyCmdDispatcher()
    await event_manager.add_dispatcher(command_d)
    await event_manager.add_dispatcher(TwitchRedeemDispatcher())
    event_manager.add_event_processor(MessageEvent, filter_message_event_text)
    twitch = TwitchEventSource(
        os.getenv("TWITCH_APP_ID", ""),
        os.getenv("TWITCH_APP_SECRET", ""),
        os.getenv("BOT_USER_ID", ""),
        [os.getenv("OWNER_TWITCH_ID", "")],
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
    # tasks.append(event_manager.add_event_source(twitch))
    ravenfall = RavenfallEventSource(
        ravenfall_config=[
            RavenfallConfig(
                twitch_id="756734432",
                twitch_login="abrokecube",
                query_server_base_url="http://pc3-server/rf_query/1/",
            ),
            # RavenfallConfig(
            #     "1253884011", "borkedcube", "http://pc3-server/rf_query/2/"
            # ),
            RavenfallConfig(
                twitch_id="1312439833",
                twitch_login="cubedhelperbot",
                query_server_base_url="http://127.0.0.1:8888/ravenfall/",
                middleman_connection_id="rf_abrokecube",
            ),
        ],
        middleman_base_url="http://127.0.0.1:7101/",
    )
    tasks.append(event_manager.add_event_source(ravenfall))

    tasks.append(event_manager.add_cog(TestingCog))
    tasks.append(event_manager.add_cog(HelpCog))
    tasks.append(event_manager.add_cog(ExampleCog))
    tasks.append(event_manager.add_cog(PrometheusTestCog))

    await update_schema()
    tasks.append(global_ctx.register_service(DatabaseService()))
    tasks.append(global_ctx.register_service(RemoteBotService()))
    tasks.append(global_ctx.register_service(ConfigService("config.toml")))
    tasks.append(global_ctx.register_service(WebService()))
    tasks.append(global_ctx.register_service(PrometheusService()))
    __ = await asyncio.gather(*tasks)

    # __ = await twitch.authenticate_user(
    #     os.getenv("OWNER_TWITCH_ID", ""),
    #     [AuthScope.CHANNEL_BOT, AuthScope.CHANNEL_MANAGE_REDEMPTIONS],
    # )
    # __ = await twitch.add_eventsub_subscriptions(
    #     os.getenv("OWNER_TWITCH_ID", ""),
    #     EventSubTopic.CHANNEL_POINTS_CUSTOM_REWARD_REDEMPTION_ADD,
    # )
    # __ = await twitch.join_chat(
    #     channel_id=os.getenv("OWNER_TWITCH_ID", ""), mode=MessageReceiveMode.IRC
    # )

    logger.info("### Bot is ready ###")
    wait_forever = asyncio.Event()
    try:
        __ = await wait_forever.wait()
    except asyncio.CancelledError:
        logger.info("Bot is shutting down")
        await event_manager.teardown()
        await global_ctx.stop_all()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(run())
