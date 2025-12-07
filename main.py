from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticationStorageHelper, UserAuthenticator
from twitchAPI.type import AuthScope, ChatEvent
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatCommand
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.chat.middleware import *
from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionAddEvent
from twitchAPI import helper
from twitchAPI.type import MissingScopeException, InvalidTokenException

from dotenv import load_dotenv

import os
import asyncio
import json
from typing import List, Tuple
import logging

import ravenpy

from bot.commands import (
    Commands, Context, Command, TwitchRedeem, TwitchRedeemContext, 
    CustomRewardRedemptionStatus, CheckFailure, ArgumentError,
)
from bot.models import *
from bot.ravenfallmanager import RFChannelManager
from database.models import update_schema
from utils.logging_fomatter import setup_logging
from bot.server import SomeEndpoints
import database.utils as db_utils
from database.session import get_async_session

load_dotenv()

USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT, AuthScope.CHANNEL_MANAGE_REDEMPTIONS]
logger_config = {
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

rf_manager = None

class MyCommands(Commands):
    def __init__(self, twitch: Twitch):
        super().__init__(twitch)
    
    async def get_prefix(self, msg: ChatMessage) -> str:
        return os.getenv("BOT_COMMAND_PREFIX", "!")

async def get_tokens(user_id: int, user_name: str = None) -> Tuple[str, str]:
    save_new_tokens = True
    async with get_async_session() as session:
        access_token, refresh_token = await db_utils.get_tokens(session, user_id)
        if access_token is not None:
            save_new_tokens = False

    while True:
        twitch = await Twitch(os.getenv("TWITCH_CLIENT"), os.getenv("TWITCH_SECRET"))
        if access_token is None:
            auth = UserAuthenticator(twitch, USER_SCOPE, True)
            print(f"Please authenticate with the Twitch account: {user_name or user_id}")
            result = await auth.authenticate(use_browser=False)
            if result is not None:
                access_token, refresh_token = result
            else:
                continue

        try:
            await twitch.set_user_authentication(access_token, USER_SCOPE, refresh_token)
            user = None
            if save_new_tokens:
                user = await helper.first(twitch.get_users())
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
                return access_token, refresh_token
            else:
                print("Token does not match user, please try again")
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
        else:
            return access_token, refresh_token
async def get_twitch_auth_instance(user_id: Union[int, str], user_name: str = None) -> Twitch:
    twitch = await Twitch(os.getenv("TWITCH_CLIENT"), os.getenv("TWITCH_SECRET"))
    access_token, refresh_token = await get_tokens(user_id, user_name)
    await twitch.set_user_authentication(access_token, USER_SCOPE, refresh_token)
    return twitch

async def run():
    def handle_loop_exception(loop, context):
        logger.error("Caught async exception: %s", context["exception"], exc_info=True)

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_loop_exception)
    
    await update_schema()
    
    twitch = await get_twitch_auth_instance(os.getenv("BOT_ID"))
    rf = ravenpy.RavenNest(os.getenv("API_USER"), os.getenv("API_PASS"))
    asyncio.create_task(rf.login())

    chat = await Chat(twitch, initial_channel=[x['channel_name'] for x in channels])

    commands = MyCommands(chat)

    async def redemption_callback(redemption: ChannelPointsCustomRewardRedemptionAddEvent):
        await commands.process_channel_point_redemption(redemption.event)

    eventsubs = []
    twitches = {}
    for channel in channels:
        if channel.get("channel_points_redeems", False):
            channel_twitch = await get_twitch_auth_instance(channel['channel_id'], channel['channel_name'])
            twitches[channel['channel_id']] = channel_twitch
            eventsub = EventSubWebsocket(channel_twitch)
            eventsub.start()
            try:
                await eventsub.listen_channel_points_custom_reward_redemption_add(
                    channel['channel_id'],
                    redemption_callback,
                )
                logger.info(f"Listening for redeems in {channel['channel_name']}")
                eventsubs.append(eventsub)
            except Exception as e:
                logger.error(f"Error listening for redeems in {channel['channel_name']}: {e}")
                await eventsub.stop()

    commands.twitches = twitches

    def load_cogs():
        if os.getenv("COMMAND_TESTING") == "1":
            from bot.cogs.example import ExampleCog
            commands.load_cog(ExampleCog)
        else:
            from bot.cogs.info import InfoCog
            commands.load_cog(InfoCog, rf_manager=rf_manager)
            from bot.cogs.testing import TestingCog
            commands.load_cog(TestingCog)
            from bot.cogs.game import GameCog
            commands.load_cog(GameCog, rf_manager=rf_manager)
            from bot.cogs.testing_rf import TestingRFCog
            commands.load_cog(TestingRFCog, rf_manager=rf_manager)
            from bot.cogs.bot import BotStuffCog
            commands.load_cog(BotStuffCog, rf_manager=rf_manager)
            from bot.cogs.redeem import RedeemCog
            commands.load_cog(RedeemCog)
            from bot.cogs.redeem_rf import RedeemRFCog
            commands.load_cog(RedeemRFCog, rf_manager=rf_manager)
        from bot.cogs.help import HelpCog
        commands.load_cog(HelpCog, commands=commands)
        
    async def on_message(message: ChatMessage):
        # logger.debug("%s: %s: %s", message.room.name, message.user.name, message.text)
        await commands.process_twitch_message(message)
        await rf_manager.event_twitch_message(message)

    async def on_ready(ready_event: EventData):
        global rf_manager
        rf_manager = RFChannelManager(channels, chat, rf)
        if not os.getenv("DISABLE_RAVENFALL_INTEGRATION", "").lower() in ("1", "true"):
            await rf_manager.start()
        load_cogs()
        logger.info("Bot is ready for work")
        chat.register_event(ChatEvent.MESSAGE, on_message)
        
        server = SomeEndpoints(rf_manager, os.getenv("SERVER_HOST", "0.0.0.0"), os.getenv("SERVER_PORT", 8080))
        await server.start()

    chat.register_event(ChatEvent.READY, on_ready)

    chat.start()

    try:
        while True:
            await asyncio.sleep(9999)
    except asyncio.CancelledError:
        logger.info("Bot is shutting down")
        chat.stop()
        for eventsub in eventsubs:
            await eventsub.stop()
        await rf_manager.stop()
        await twitch.close()

if __name__ == "__main__":
    asyncio.run(run())
