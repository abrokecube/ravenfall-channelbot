import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, override

from dotenv import load_dotenv
from pydantic import BaseModel

from bot.cogs.bot import BotStuffCog
from bot.cogs.example import ExampleCog
from bot.cogs.help import HelpCog
from bot.cogs.process_watchdog import ProcessWatchdogCog
from bot.cogs.testing import TestingCog
from bot.core.components import EventManager, GlobalContext
from bot.db.models import update_schema
from bot.db.service import DatabaseService
from bot.integrations.chat_messages import MessageEvent
from bot.integrations.chat_messages.event_processors import filter_message_event_text
from bot.integrations.commands import CommandDispatcher
from bot.integrations.process_manager import ProcessEventSource
from bot.integrations.ravenfall.event_sources import RavenfallEventSource
from bot.integrations.twitch.dispatchers import TwitchRedeemDispatcher
from bot.integrations.twitch.enums import EventSubTopic, MessageReceiveMode
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


class BotConfig(BaseModel):
    """Top-level bot config."""

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
bot_config = config_service.get_table("bot", BotConfig)

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

    tasks: list[Awaitable[None]] = []
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
    tasks.append(event_manager.add_event_source(twitch))
    tasks.append(event_manager.add_event_source(RavenfallEventSource()))
    tasks.append(event_manager.add_event_source(ProcessEventSource()))

    tasks.append(event_manager.add_cog(TestingCog))
    tasks.append(event_manager.add_cog(HelpCog))
    tasks.append(event_manager.add_cog(ExampleCog))
    tasks.append(event_manager.add_cog(ProcessWatchdogCog))
    tasks.append(event_manager.add_cog(BotStuffCog))

    await update_schema()
    tasks.append(global_ctx.register_service(DatabaseService()))
    tasks.append(global_ctx.register_service(RemoteBotService()))
    tasks.append(global_ctx.register_service(config_service))
    tasks.append(global_ctx.register_service(WebService()))
    tasks.append(global_ctx.register_service(PrometheusService()))
    __ = await asyncio.gather(*tasks)

    __ = await twitch.authenticate_user(
        bot_config.owner_twitch_id,
        [AuthScope.CHANNEL_BOT, AuthScope.CHANNEL_MANAGE_REDEMPTIONS],
    )
    __ = await twitch.add_eventsub_subscriptions(
        bot_config.owner_twitch_id,
        EventSubTopic.CHANNEL_POINTS_CUSTOM_REWARD_REDEMPTION_ADD,
    )
    __ = await twitch.join_chat(
        channel_id=bot_config.owner_twitch_id, mode=MessageReceiveMode.IRC
    )

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
