from collections.abc import Awaitable
from dotenv import load_dotenv

from bot.core.components import GlobalContext
from bot.core.components import EventManager
from bot.db.service import DatabaseService
from bot.integrations.chat_messages.events import MessageEvent
from bot.integrations.commands.dispatchers import CommandDispatcher
from bot.integrations.chat_messages.event_processors import filter_message_event_text
from bot.integrations.twitch.dispatchers import TwitchRedeemDispatcher
from bot.integrations.twitch.enums import EventSubTopic, MessageReceiveMode
from bot.integrations.twitch.event_sources import AuthScope, TwitchEventSource

_ = load_dotenv()


import os
import asyncio
import json
import logging
from utils.logging_fomatter import setup_logging
from typing import cast, override, Any
from bot.db.models import update_schema

with open("pid", "w") as f:
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
    "asyncio": {
        "console_level": logging.INFO,
    },
    "twitchAPI": {
        "filename": "twitchAPI.log",
        "console_level": logging.INFO,
    },
    "middleman": {
        "filename": "middleman.log",
        "console_level": logging.INFO,
    },
    "aiosqlite": {
        "filename": "database.log",
        "console_level": logging.INFO,
    },
    "new_message_processor": {
        "filename": "rfmsgproc.log",
        "console_level": logging.INFO,
    },
    "aiohttp.access": {
        "filename": "httpserver.log",
        "console_level": logging.WARNING,
    },
    "bot.server": {
        "filename": "httpserver.log",
        "console_level": logging.WARNING,
    },
    "utils.runshell": {
        "filename": "runshell.log",
        "console_level": logging.WARNING,
    },
    "bot.ravenfallloc": {
        "filename": "rfloc.log",
        "console_level": logging.WARNING,
    },
    "bot.ravenfallchannel": {
        "filename": "rfchannels.log",
        "console_level": logging.INFO,
    },
    "bot.ravenfallmanager": {
        "filename": "rfchannels.log",
        "console_level": logging.INFO,
    },
    "bot.commands": {
        "filename": "commands.log",
        "console_level": logging.INFO,
    },
}

log_level = os.getenv("LOG_LEVEL", "info")
default_console_logging_level = logging_level_strs.get(log_level.lower(), logging.INFO)
setup_logging(level=default_console_logging_level, loggers_config=logger_config)

logger = logging.getLogger(__name__)
if not log_level.lower() in logging_level_strs:
    logger.warning(f"Invalid logging level '{log_level}'")


class MyCmdDispatcher(CommandDispatcher):
    def __init__(self):
        super().__init__()

    @override
    async def get_prefix(self, global_context: GlobalContext, event: MessageEvent) -> str:
        return os.getenv("BOT_COMMAND_PREFIX", "!")


async def run():
    def handle_loop_exception(_: asyncio.AbstractEventLoop, context: dict[str, Any]):  # pyright: ignore [reportExplicitAny]
        logger.exception("Caught async exception: %s", context.get("exception"))

    logger.info("Setting up loop")
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_loop_exception)

    logger.info("Checking db")
    await update_schema()

    global_ctx = GlobalContext()

    async with global_ctx.service_resolution_context():
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
        tasks.append(event_manager.add_event_source(twitch))
        from bot.cogs.testing import TestingCog

        await event_manager.add_cog(TestingCog)

        await update_schema()
        global_ctx.register_service(DatabaseService, DatabaseService())
        __ = await asyncio.gather(*tasks)

    __ = await twitch.authenticate_user(
        os.getenv("OWNER_TWITCH_ID", ""),
        [AuthScope.CHANNEL_BOT, AuthScope.CHANNEL_MANAGE_REDEMPTIONS],
    )
    __ = await twitch.add_eventsub_subscriptions(
        os.getenv("OWNER_TWITCH_ID", ""),
        EventSubTopic.CHANNEL_POINTS_CUSTOM_REWARD_REDEMPTION_ADD,
    )
    __ = await twitch.join_chat(
        channel_id=os.getenv("OWNER_TWITCH_ID", ""), mode=MessageReceiveMode.IRC
    )

    logger.info("Bot is ready")
    wait_forever = asyncio.Event()
    try:
        __ = await wait_forever.wait()
    except asyncio.CancelledError:
        logger.info("Bot is shutting down")
        await event_manager.teardown()
        await global_ctx.stop_all()


if __name__ == "__main__":
    asyncio.run(run())
