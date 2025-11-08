from bot.ravenfallchannel import RFChannel
from ..commands import CommandContext, Commands, RedeemContext
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..middleman import send_to_server_and_wait_response, send_to_client
from bot.multichat_command import get_char_coins, get_char_items
from bot.message_templates import RavenBotTemplates
from database.session import get_async_session
from database.utils import get_formatted_sender_data, add_credits, get_user_credits
from dataclasses import dataclass
from bot.messageprocessor import RavenfallMessage
import logging
import json
from typing import Dict, Tuple
from ravenpy import ravenpy
from ravenpy.ravenpy import Item
import os
import asyncio
import random

logger = logging.getLogger(__name__)

@dataclass
class RavenfallResponse:
    response: RavenfallMessage
    response_id: str
    formatted_response: str

class CouldNotSendMessageError(Exception):
    pass

class CouldNotSendItemsError(Exception):
    pass

class TimeoutError(Exception):
    pass

class OutOfItemsError(Exception):
    pass

class PartialSendError(Exception):
    pass

class ItemNotFoundError(Exception):
    pass

async def get_sender_str(channel: RFChannel, sender_username: str):
    async with get_async_session() as session:
        sender = await get_formatted_sender_data(session, channel.channel_id, sender_username)
    return sender

async def send_ravenfall(channel: RFChannel, message: str, timeout: int = 20):
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

channel_coin_gift_locks: Dict[str, asyncio.Lock] = {}
async def send_coins(target_user_name: str, channel: RFChannel, amount: int):
    if channel.channel_id in channel_coin_gift_locks:
        lock = channel_coin_gift_locks[channel.channel_id]
    else:
        lock = asyncio.Lock()
        channel_coin_gift_locks[channel.channel_id] = lock

    async with lock:
        char_coins = await get_char_coins(channel.channel_id)
        total_coins = 0
        random.shuffle(char_coins["data"])
        for user in char_coins["data"]:
            if user["coins"] <= 0:
                continue
            if user["user_name"].lower() == target_user_name.lower():
                continue

            total_coins += user["coins"]

        if total_coins < amount:
            raise OutOfItemsError("Not enough coins")

        if amount != -1:
            coins_remaining = amount
        else:
            coins_remaining = total_coins
        one_coin_successful = False
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
                if not one_coin_successful:
                    raise CouldNotSendItemsError("Failed to send coins")
            if response.response_id == "gift_coins_one":
                coins_to_send = 1
            else:
                coins_to_send = int(response.response["Args"][1])
            coins_remaining -= coins_to_send
            one_coin_successful = True

        if amount != -1 and coins_remaining > 0:
            raise PartialSendError(f"Ran out of coins ({coins_remaining} remaining)")

channel_item_gift_locks: Dict[str, asyncio.Lock] = {}
async def send_items(target_user_name: str, channel: RFChannel, item_name: str, amount: int):
    if channel.channel_id in channel_coin_gift_locks:
        lock = channel_coin_gift_locks[channel.channel_id]
    else:
        lock = asyncio.Lock()
        channel_coin_gift_locks[channel.channel_id] = lock

    async with lock:
        item_search_results = ravenpy.search_item(item_name, limit=1)
        if not item_search_results:
            raise ItemNotFoundError("Item not found")
        if item_search_results[0][1] < 85:
            raise ItemNotFoundError("Item not found")
        item = item_search_results[0][0]

        char_items = await get_char_items(channel.channel_id)
        total_items = 0
        user_items = []
        for user in char_items["data"]:
            if user["user_name"].lower() == target_user_name.lower():
                continue
            for user_item in user["items"]:
                if user_item['soulbound'] or user_item['equipped']:
                    continue
                if user_item["id"] == item.id:
                    total_items += user_item["amount"]
                    user_items.append({
                        "user_name": user["user_name"],
                        "amount": user_item["amount"],
                    })
                    break

        random.shuffle(user_items)
        if total_items < amount:
            raise OutOfItemsError("Not enough items")

        if amount != -1:
            items_remaining = amount
        else:
            items_remaining = total_items
        one_item_successful = False
        for user_item in user_items:
            if items_remaining <= 0:
                break
            items_to_send = min(items_remaining, user_item["amount"])

            logger.info(f"Sending {items_to_send}x {item.name} to {target_user_name} from {user_item['user_name']}")
            response = await send_ravenfall(
                channel, RavenBotTemplates.gift_item(
                    sender = await get_sender_str(channel, user_item["user_name"]),
                    recipient_user_name = target_user_name,
                    item_name = item.name,
                    item_count = items_to_send,
                )
            )
            if response.response_id not in ("gift", "gift_item_not_owned"):
                logger.info(f"Failed to send {item.name} to {target_user_name} from {user_item['user_name']}: {response.response_id}")
                if not one_item_successful:
                    raise CouldNotSendItemsError("Failed to send items")
            if response.response_id == "gift_item_not_owned":
                items_to_send = 0
            else:
                items_to_send = int(response.response["Args"][0])
            items_remaining -= items_to_send
            one_item_successful = True

        if amount != -1 and items_remaining > 0:
            raise PartialSendError(f"Ran out of items ({items_remaining} remaining)")


class RedeemRFCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager

    async def send_coins_redeem(self, ctx: RedeemContext, amount: int):
        channel = self.rf_manager.get_channel(channel_id=ctx.redemption.broadcaster_user_id)
        if channel is None:
            return
        await ctx.send(f"Sending {amount:,} coins to {ctx.redemption.user_login}...")
        try:
            await send_coins(ctx.redemption.user_login, channel, amount)
        except (CouldNotSendMessageError, CouldNotSendItemsError, OutOfItemsError, TimeoutError) as e:
            await ctx.cancel()
            logger.error(f"Error in coin redeem: {e}")
            await ctx.send(f"❌ Error: {e}. Please try again later. You have been refunded.")
            return
        except PartialSendError as e:
            logger.error(f"Partial send error in coin redeem: {e}")
            await ctx.send(f"❌ {e}. pinging @{os.getenv("OWNER_TWITCH_USERNAME")}")
            return
        except Exception as e:
            await ctx.cancel()
            logger.error(f"Unknown error occured in coin redeem: {e}")
            await ctx.send(f"❌ An unknown error occured. Please try again later. You have been refunded.")
            return
        await ctx.fullfill()
    
    @Cog.redeem(name="Recieve 25,000 coins")
    async def coins_25_000(self, ctx: RedeemContext):
        await self.send_coins_redeem(ctx, 25000)

    @Cog.redeem(name="Recieve 250,000 coins")
    async def coins_250_000(self, ctx: RedeemContext):
        await self.send_coins_redeem(ctx, 250000)

    @Cog.redeem(name="Recieve 1,000,000 coins")
    async def coins_1_000_000(self, ctx: RedeemContext):
        await self.send_coins_redeem(ctx, 1000000)

    @Cog.redeem(name="Recieve 100 item credits")
    async def item_credits_100(self, ctx: RedeemContext):
        async with get_async_session() as session:
            await add_credits(session, ctx.redemption.user_id, 100, "Item credits redeem")

    @Cog.redeem(name="Recieve 500 item credits")
    async def item_credits_500(self, ctx: RedeemContext):
        async with get_async_session() as session:
            await add_credits(session, ctx.redemption.user_id, 500, "Item credits redeem")

    @Cog.redeem(name="Recieve 3000 item credits")
    async def item_credits_3000(self, ctx: RedeemContext):
        async with get_async_session() as session:
            await add_credits(session, ctx.redemption.user_id, 3000, "Item credits redeem")

    @Cog.redeem(name="Recieve 15,000 item credits")
    async def item_credits_15_000(self, ctx: RedeemContext):
        async with get_async_session() as session:
            await add_credits(session, ctx.redemption.user_id, 15000, "Item credits redeem")

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
    
    @Cog.command(name="giftto")
    async def giftto(self, ctx: CommandContext):
        if os.getenv("OWNER_TWITCH_ID") != ctx.msg.user.id:
            return

        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        args = ctx.parameter.split()

        if len(args) < 2:
            await ctx.reply("Usage: !giftto <user> <item> [count]")
            return

        count = 1
        if args[-1].isdigit():
            count = int(args.pop())
        elif args[-1].lower() == "all":
            count = -1
            args.pop()
        recipient_name = args[0]
        item_name = " ".join(args[1:])
        
        try:
            if item_name.lower() == "coins":
                await send_coins(recipient_name, channel, count)
            else:
                await send_items(recipient_name, channel, item_name, count)
        except (CouldNotSendMessageError, CouldNotSendItemsError, OutOfItemsError, TimeoutError, ItemNotFoundError, PartialSendError) as e:
            logger.error(f"Error in command: {e}")
            await ctx.send(f"❌ Error: {e}")
            return
        except Exception as e:
            logger.error(f"Unknown error occured in command: {e}")
            await ctx.send(f"❌ An unknown error occured. Please try again later.")
            return
            
        await asyncio.sleep(0.5)
        await ctx.reply("Okay")

def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(RedeemRFCog, rf_manager=rf_manager, **kwargs)