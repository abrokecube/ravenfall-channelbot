from bot.ravenfallchannel import RFChannel
from ..commands import CommandContext, Commands, RedeemContext, CustomRewardRedemptionStatus
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..middleman import send_to_server_and_wait_response, send_to_client
from bot.multichat_command import get_char_coins, get_char_items
from bot.message_templates import RavenBotTemplates
from database.session import get_async_session
from database.utils import get_formatted_sender_data
from dataclasses import dataclass
from bot.messageprocessor import RavenfallMessage
import logging
import json
from typing import Tuple
from ravenpy import ravenpy
from ravenpy.ravenpy import Item

logger = logging.getLogger(__name__)

@dataclass
class RavenfallResponse:
    response: RavenfallMessage
    response_id: str
    formatted_response: str

class CouldNotSendMessageError(Exception):
    pass

class CouldNotSendCoinsError(Exception):
    pass

class TimeoutError(Exception):
    pass

class OutOfCoinsError(Exception):
    pass

async def get_sender_str(channel: RFChannel, sender_username: str):
    async with get_async_session() as session:
        sender = await get_formatted_sender_data(session, channel.channel_id, sender_username)
    return sender

async def send_ravenfall(channel: RFChannel, message: str, timeout: int = 10):
    response = await send_to_server_and_wait_response(channel.middleman_connection_id, message, timeout=timeout)
    if not response["success"]:
        logger.error(f"Could not talk to Ravenfall: {response}")
        raise CouldNotSendMessageError("Could not talk to Ravenfall")
    if response["timeout"]:
        raise TimeoutError("Timed out waiting for response")
    response_dict = response["responses"][0]
    response_dict["CorrelationId"] = None
    await send_to_client(channel.middleman_connection_id, json.dumps(response_dict))

    match = channel.rfloc.identify_string(response_dict['Format'])
    response_id = None
    if match is not None:
        response_id = match.key
    return RavenfallResponse(
        response = response_dict,
        response_id = response_id,
        formatted_response = channel.rfloc.translate_string(response_dict['Format'], response_dict['Args'], match).strip()
    )

async def get_coins_count(channel: RFChannel):
    char_coins = await get_char_coins(channel.channel_id)
    total_coins = 0
    for user in char_coins["data"]:
        if user["coins"] <= 0:
            continue
        total_coins += user["coins"]
    return total_coins



async def get_item_count(channel: RFChannel, item_name: str) -> Tuple[Item, int]:
    item_search_results = ravenpy.search_item(item_name, limit=1)
    if not item_search_results:
        return None, 0
    if item_search_results[0][1] < 85:
        return None, 0
    item = item_search_results[0][0]

    char_items = await get_char_items(channel.channel_id)
    total_items = 0
    for user in char_items["data"]:
        for user_item in user["items"]:
            if user_item['soulbound'] or user_item['equipped']:
                continue
            if user_item["id"] == item.id:
                total_items += user_item["amount"]
                break
    return item, total_items

async def send_coins(target_user_name: str, channel: RFChannel, amount: int):
    char_coins = await get_char_coins(channel.channel_id)
    total_coins = 0
    for user in char_coins["data"]:
        if user["coins"] <= 0:
            continue
        total_coins += user["coins"]

    if total_coins < amount:
        raise OutOfCoinsError("Not enough coins")

    coins_remaining = amount
    for user in char_coins["data"]:
        if coins_remaining <= 0:
            break
        if user["coins"] <= 0:
            continue
        if user["user_name"].lower() == target_user_name.lower():
            continue
        coins_to_send = min(coins_remaining, user["coins"])

        logger.info(f"Sending {coins_to_send} coins to {target_user_name} from {user['user_name']}")
        response = await send_ravenfall(
            channel, RavenBotTemplates.gift_item(
                sender = await get_sender_str(channel, user["user_name"]),
                recipient_user_name = target_user_name,
                item_name = "coins",
                item_count = coins_to_send,
            )
        )
        if response.response_id not in ("gift_coins", "gift_coins_one"):
            logger.info(f"Failed to send coins to {target_user_name} from {user['user_name']}: {response.response_id}")
            raise CouldNotSendCoinsError("Failed to send coins")

        coins_remaining -= coins_to_send

    if coins_remaining > 0:
        raise OutOfCoinsError("Ran out of coins")
        

class RedeemRFCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager

    @Cog.redeem(name="Recieve 25,000 coins")
    async def coins_25_000(self, ctx: RedeemContext):
        channel = self.rf_manager.get_channel(channel_id=ctx.redemption.broadcaster_user_id)
        if channel is None:
            return
        try:
            await send_coins(ctx.redemption.broadcaster_user_login, channel, 25000)
        except (CouldNotSendMessageError, CouldNotSendCoinsError, OutOfCoinsError, TimeoutError) as e:
            await ctx.update_status(CustomRewardRedemptionStatus.CANCELED)
            logger.error(f"Error in coins_25_000: {e}")
            await ctx.send(f"❌ Error: {e} - points have been refunded")
            return
        except Exception as e:
            await ctx.update_status(CustomRewardRedemptionStatus.CANCELED)
            logger.error(f"Unknown error occured in coins_25_000: {e}")
            await ctx.send(f"❌ Unknown error occured - points have been refunded")
            return
        await ctx.update_status(CustomRewardRedemptionStatus.FULFILLED)
    
    @Cog.command(name="stock coins")
    async def stock_coins(self, ctx: CommandContext):
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        count = await get_coins_count(channel)
        await ctx.reply(f"There are currently {count:,} coins in stock.")

    @Cog.command(name="stock")
    async def stock_item(self, ctx: CommandContext):
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        item, count = await get_item_count(channel, ctx.parameter)
        if item is None:
            await ctx.reply(f"Could not identify item")
            return
        await ctx.reply(f"There is currently {count:,}× {item.name} in stock.")


def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(RedeemRFCog, rf_manager=rf_manager, **kwargs)