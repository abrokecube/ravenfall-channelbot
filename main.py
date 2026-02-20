from twitchAPI.twitch import Twitch, TwitchUser
from twitchAPI.oauth import UserAuthenticationStorageHelper, UserAuthenticator
from twitchAPI.type import AuthScope, ChatEvent
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatCommand
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.chat.middleware import *
from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionAddEvent
from twitchAPI import helper
from twitchAPI.type import MissingScopeException, InvalidTokenException

from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
import json
from typing import List, Tuple
import logging

import ravenpy

from bot.commands.global_context import GlobalContext
from bot.commands.event_sources import TwitchAPIEventSource
from bot.commands.event_manager import EventManager
from bot.commands.dispatchers import CommandDispatcher, TwitchRedeemDispatcher

from bot.models import *
from bot.ravenfallmanager import RFChannelManager
from database.models import update_schema
from utils.logging_fomatter import setup_logging
from bot.server import SomeEndpoints
# from bot.chat_system import ChatManager
import database.utils as db_utils
from database.session import get_async_session

with open('pid', 'w') as f:
    f.write(str(os.getpid()))

TWITCH_APP_SCOPES = [
    AuthScope.USER_WRITE_CHAT
]
TWITCH_BOT_USER_SCOPES = [
    AuthScope.CHAT_READ,
    AuthScope.CHAT_EDIT,
    AuthScope.USER_BOT,
    AuthScope.USER_WRITE_CHAT,
]
TWITCH_CHANNEL_SCOPES = [
    AuthScope.CHANNEL_MANAGE_REDEMPTIONS,
    AuthScope.MODERATOR_MANAGE_ANNOUNCEMENTS,
    AuthScope.CHANNEL_BOT
]
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
setup_logging(level=logging.DEBUG, loggers_config=logger_config)
logger = logging.getLogger(__name__)

with open("channels.json", "r") as f:
    channels: List[Channel] = json.load(f)
for channel in channels:
    channel["rf_query_url"] = channel["rf_query_url"].rstrip("/")
    # Set default command prefix if not specified
    if 'command_prefix' not in channel:
        channel['command_prefix'] = '!'

rf_manager: RFChannelManager = None

async def setup_twitch(global_ctx: GlobalContext, event_manager: EventManager):
    async def get_twitch_auth_instance(user_id: int | str, user_name: Optional[str] = None, scopes: List[AuthScope] = TWITCH_CHANNEL_SCOPES) -> Twitch:
        save_new_tokens = True
        async with get_async_session() as session:
            access_token, refresh_token = await db_utils.get_tokens(session, user_id)
            if access_token is not None:
                save_new_tokens = False

        while True:
            twitch = await Twitch(os.getenv("TWITCH_APP_ID", ""), os.getenv("TWITCH_APP_SECRET"), target_app_auth_scope=TWITCH_APP_SCOPES)
            if access_token is None:
                auth = UserAuthenticator(twitch, scopes, True)
                print(f"Auth scopes: {', '.join([x.value for x in scopes])}")
                print(f"Please authenticate with the Twitch account: {user_name or user_id}")
                result = await auth.authenticate(use_browser=False)
                if result is not None:
                    access_token, refresh_token = result
                else:
                    continue

            try:
                await twitch.set_user_authentication(access_token, scopes, refresh_token)
                user: TwitchUser | None = None
                if save_new_tokens:
                    user = await helper.first(twitch.get_users())
                    if isinstance(user, TwitchUser):
                        async with get_async_session() as session:
                            await db_utils.update_tokens(session, user.id, access_token, refresh_token, user.login)
            except MissingScopeException:
                print("Token is missing scopes")
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
            except InvalidTokenException:
                print("Invalid token")
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
            except Exception as e:
                print(f"Error setting user authentication: {e}")
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
            
            if user is not None:
                if user.id == str(user_id):
                    return twitch
                else:
                    print("Token does not match user, please try again")
                    access_token = None
                    refresh_token = None
                    save_new_tokens = True
                    continue
            else:
                return twitch
                
    logger.info("Getting twitch info")
    twitch = await get_twitch_auth_instance(os.getenv("BOT_USER_ID", ""), scopes=TWITCH_BOT_USER_SCOPES)

    logger.info("Initializing twitch chat instance")
    chat = await Chat(twitch, initial_channel=[x['channel_name'] for x in channels])
    twitch_user = await helper.first(twitch.get_users())
    
    global_ctx.bot_twitch = twitch
    global_ctx.twitch_chat = chat
    
    twitch_admin_uids = set((os.getenv("BOT_USER_ID"), os.getenv("OWNER_TWITCH_ID")))
    twitch_source = TwitchAPIEventSource(chat, twitch, {}, twitch_user, twitch_admin_uids)
    event_manager.add_event_source(twitch_source)
    
    async def redemption_callback(redemption: ChannelPointsCustomRewardRedemptionAddEvent):
        await twitch_source.on_channel_point_redemption(redemption.event)
        
    async def on_message(message: ChatMessage):
        global rf_manager
        await twitch_source.on_message(message)
        if rf_manager:
            await rf_manager.event_twitch_message(message)

    async def on_ready(ready_event: EventData):
        logger.info("Twitch chat is ready")
        chat.register_event(ChatEvent.MESSAGE, on_message)
        
    chat.register_event(ChatEvent.READY, on_ready)

    chat.start()
        
    twitches: Dict[str, Twitch] = {}
    twitch_source.channel_twitches = twitches
    global_ctx.channel_twitches = twitches
    
    twitch_eventsubs = []
    global_ctx._twitch_channel_eventsubs = twitch_eventsubs
    
    logger.info("Subscribing to twitch eventsub")
    for channel in channels:
        if channel.get("channel_points_redeems", False):
            channel_twitch = await get_twitch_auth_instance(channel['channel_id'], channel['channel_name'], TWITCH_CHANNEL_SCOPES)
            twitches[channel['channel_id']] = channel_twitch
            eventsub = EventSubWebsocket(channel_twitch)
            eventsub.start()
            try:
                await eventsub.listen_channel_points_custom_reward_redemption_add(
                    channel['channel_id'],
                    redemption_callback,
                )
                logger.info(f"Listening for redeems in {channel['channel_name']}")
                twitch_eventsubs.append(eventsub)
            except Exception as e:
                logger.error(f"Error listening for redeems in {channel['channel_name']}: {e}", exc_info=True)
                await eventsub.stop()    

class MyCmdDispatcher(CommandDispatcher):
    def __init__(self):
        super().__init__()
        
    async def get_prefix(self, global_context, event):
        return os.getenv("BOT_COMMAND_PREFIX", "!")

async def run():
    def handle_loop_exception(loop, context):
        logger.error("Caught async exception: %s", context["exception"], exc_info=True)

    logger.info("Setting up loop")
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_loop_exception)
    
    logger.info("Checking db")
    await update_schema()
        
    rf = ravenpy.RavenNest(os.getenv("RAVENFALL_API_USER"), os.getenv("RAVENFALL_API_PASS"))
    asyncio.create_task(rf.login())
    
    # internal_chat = ChatManager()
    logger.info("Initializing event system")

    global_ctx = GlobalContext()
    event_manager = EventManager(global_ctx)
    command_dispatcher = MyCmdDispatcher()
    event_manager.add_dispatcher(command_dispatcher)
    twitch_redeem_dispatcher = TwitchRedeemDispatcher()
    event_manager.add_dispatcher(twitch_redeem_dispatcher)
    
    global_ctx.ravennest = rf

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

    await setup_twitch(global_ctx, event_manager)

    rf_manager = RFChannelManager(channels, global_ctx.twitch_chat, rf, global_ctx.channel_twitches)
    if not os.getenv("DISABLE_RAVENFALL_INTEGRATION", "").lower() in ("1", "true"):
        await rf_manager.start()

    global_ctx.ravenfall_manager = rf_manager

    server = SomeEndpoints(rf_manager, None, os.getenv("PRIVATE_SERVER_HOST", "0.0.0.0"), int(os.getenv("PRIVATE_SERVER_PORT", 8080)))
    await server.start()

    try:
        while True:
            await asyncio.sleep(9999)
    except asyncio.CancelledError:
        logger.info("Bot is shutting down")
        global_ctx.twitch_chat.stop()
        tasks = []
        for twitch in global_ctx.channel_twitches.values():
            tasks.append(twitch.close())
        for eventsub in global_ctx._twitch_channel_eventsubs:
            tasks.append(eventsub.stop())
        tasks.append(rf_manager.stop())
        tasks.append(global_ctx.bot_twitch.close())
        tasks.append(event_manager.stop_all())
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logging.error(f"Error occurred while shutting down: {r}")

if __name__ == "__main__":
    asyncio.run(run())
