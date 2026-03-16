from typing import Any, override, cast
from database.service import DatabaseService

from dotenv import load_dotenv
_ = load_dotenv()

import os
import asyncio
import json
import logging

import ravenpy

from bot.core.global_context import GlobalContext
from bot.core.event_sources import TwitchAPIEventSource
from bot.core.event_manager import EventManager
from bot.core.dispatchers import CommandDispatcher, TwitchRedeemDispatcher
from bot.core.events import MessageEvent

from bot.models import *
from bot.ravenfallmanager import RFChannelManager
from database.models import update_schema
from utils.logging_fomatter import setup_logging
from bot.server import SomeEndpoints

with open('pid', 'w') as f:
    _ = f.write(str(os.getpid()))

logger_config = {
    'asyncio': {
        'console_level': logging.INFO,
    },
    'twitchAPI': {
        'filename': "twitchAPI.log",
        'console_level': logging.INFO,
    },
    'middleman': {
        'filename': "middleman.log",
        'console_level': logging.INFO,
    },
    'aiosqlite': {
        'filename': "database.log",
        'console_level': logging.INFO,
    },
    'new_message_processor': {
        'filename': "rfmsgproc.log",
        'console_level': logging.INFO,
    },
    'aiohttp.access': {
        'filename': "httpserver.log",
        'console_level': logging.WARNING,
    },
    'bot.server': {
        'filename': "httpserver.log",
        'console_level': logging.WARNING,
    },
    'utils.runshell': {
        'filename': "runshell.log",
        'console_level': logging.WARNING,
    },
    'bot.ravenfallloc': {
        'filename': 'rfloc.log',
        'console_level': logging.WARNING,
    },
    'bot.ravenfallchannel': {
        'filename': 'rfchannels.log',
        'console_level': logging.INFO,
    },
    'bot.ravenfallmanager': {
        'filename': 'rfchannels.log',
        'console_level': logging.INFO,
    },
    'bot.commands': {
        'filename': 'commands.log',
        'console_level': logging.INFO,
    }
}
logging_level_strs = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL
}
log_level = os.getenv("LOG_LEVEL", "info")
default_console_logging_level = logging_level_strs.get(log_level.lower(), logging.INFO)
setup_logging(level=default_console_logging_level, loggers_config=logger_config)

logger = logging.getLogger(__name__)
if not log_level.lower() in logging_level_strs:
    logger.warning(f"Invalid logging level '{log_level}'")

with open("channels.json", "r") as f:
    channels: list[Channel] = cast(list[Channel], json.load(f))
for channel in channels:
    channel["rf_query_url"] = channel["rf_query_url"].rstrip("/")
    # Set default command prefix if not specified
    if 'command_prefix' not in channel:
        channel['command_prefix'] = '!'

rf_manager: RFChannelManager | None = None

class MyCmdDispatcher(CommandDispatcher):
    def __init__(self):
        super().__init__()
    
    @override
    async def get_prefix(self, global_context: GlobalContext, event: MessageEvent) -> str:
        return os.getenv("BOT_COMMAND_PREFIX", "!")

async def run():
    def handle_loop_exception(_: asyncio.AbstractEventLoop, context: dict[str, Any]):  # pyright: ignore [reportExplicitAny]
        logger.error("Caught async exception: %s", context.get("exception"), exc_info=True)

    logger.info("Setting up loop")
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_loop_exception)
    
    logger.info("Checking db")
    await update_schema()
        
    rf = ravenpy.RavenNest(os.getenv("RAVENFALL_API_USER") or "", os.getenv("RAVENFALL_API_PASS") or "")
    _ = asyncio.create_task(rf.login())
    
    logger.info("Initializing event system")

    global_ctx = GlobalContext()
    db_service = DatabaseService()
    global_ctx.register_service(DatabaseService, db_service)

    event_manager = EventManager(global_ctx)
    command_dispatcher = MyCmdDispatcher()
    await event_manager.add_dispatcher(command_dispatcher)
    twitch_redeem_dispatcher = TwitchRedeemDispatcher()
    await event_manager.add_dispatcher(twitch_redeem_dispatcher)
    
    global_ctx.register_service(ravenpy.RavenNest, rf)

    from bot.cogs.help import HelpCog
    await event_manager.add_cog(HelpCog)
    
    if os.getenv("COMMAND_TESTING") == "1":
        from bot.cogs.example import ExampleCog
        await event_manager.add_cog(ExampleCog)
    from bot.cogs.testing import TestingCog
    await event_manager.add_cog(TestingCog)
    from bot.cogs.redeem import RedeemCog
    await event_manager.add_cog(RedeemCog)
    from bot.cogs.redeem_rf import RedeemRFCog
    await event_manager.add_cog(RedeemRFCog)
    rfwebops = os.getenv("WEBOPS_URL", "http://pc2-mobile:7102")
    from bot.cogs.game import GameCog
    await event_manager.add_cog(GameCog, rf_webops_url=rfwebops)
    from bot.cogs.info import InfoCog
    await event_manager.add_cog(InfoCog)
    from bot.cogs.bot import BotStuffCog
    watchers = os.getenv("WATCHER_URLS", "http://127.0.0.1:8110").split(",")
    await event_manager.add_cog(BotStuffCog, watcher_urls=watchers)
    from bot.cogs.debug import DebugCog
    await event_manager.add_cog(DebugCog)

    logger.info("Checking db after cog imports")
    await update_schema()

    twitch_admin_uids = set((os.getenv("BOT_USER_ID", ""), os.getenv("OWNER_TWITCH_ID", "")))
    twitch_source = TwitchAPIEventSource(
        channels,
        twitch_admin_uids,
        bot_user_id=os.getenv("BOT_USER_ID", ""),
        twitch_app_id=os.getenv("TWITCH_APP_ID", ""),
        twitch_app_secret=os.getenv("TWITCH_APP_SECRET", "")
    )
    await event_manager.add_event_source(twitch_source)

    rf_manager = RFChannelManager(channels, global_ctx)
    if not os.getenv("DISABLE_RAVENFALL_INTEGRATION", "").lower() in ("1", "true"):
        await rf_manager.start()

    global_ctx.register_service(RFChannelManager, rf_manager)

    server = SomeEndpoints(rf_manager, os.getenv("PRIVATE_SERVER_HOST", "0.0.0.0"), int(os.getenv("PRIVATE_SERVER_PORT", 8080)))
    await server.start()

    try:
        while True:
            await asyncio.sleep(9999)
    except asyncio.CancelledError:
        logger.info("Bot is shutting down")
        tasks = [event_manager.stop_all()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logging.error(f"Error occurred while shutting down: {r}")

if __name__ == "__main__":
    asyncio.run(run())
