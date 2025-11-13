from bot.ravenfallchannel import RFChannel
from ..commands import CommandContext, Commands, RedeemContext
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..middleman import send_to_server_and_wait_response, send_to_client, send_to_server
from ..ravenfallloc import pl
from bot.multichat_command import get_char_coins, get_char_items
from bot.message_templates import RavenBotTemplates
from database.session import get_async_session
from database.utils import get_formatted_sender_data, add_credits, get_user_credits, get_channel
from dataclasses import dataclass
from bot.messageprocessor import RavenfallMessage
import logging
import json
from typing import Dict, Tuple, List
from ravenpy import ravenpy
from ravenpy.ravenpy import Item
import os
import asyncio
import random
import json
from twitchAPI.helper import first
from utils.utils import upload_to_pastes
from bot.ravenfallrestarttask import RestartReason
import re
from utils.routines import routine
from datetime import timedelta, datetime, timezone
from bot.models import Player
from database.models import UserCreditIdleEarn, Character, Channel
from sqlalchemy import select

logger = logging.getLogger(__name__)

@dataclass
class RavenfallResponse:
    response: RavenfallMessage
    response_id: str
    formatted_response: str

class BaseItemSendException(Exception):
    def __init__(self, message: str, items_sent: int = 0):
        super().__init__(message)
        self.message = message
        self.items_sent = items_sent

class CouldNotSendMessageError(BaseItemSendException):
    pass

class CouldNotSendItemsError(BaseItemSendException):
    pass

class TimeoutError(BaseItemSendException):
    pass

class OutOfItemsError(BaseItemSendException):
    pass

class PartialSendError(BaseItemSendException):
    pass

class ItemNotFoundError(BaseItemSendException):
    pass

class RecipientNotFoundError(BaseItemSendException):
    pass

def fill_whitespace(text: str, pattern: str = ". "):
    """
    Replace whitespace runs with a repeated pattern, keeping a single real space
    at each edge of the run. The total length of the run is preserved.

    Example:
        "a          b" -> "a . . . .  b"
    """
    def repl(m):
        run = m.group(0)
        run_len = len(run)
        if run_len <= 2:
            # Too short to fit pattern inside — leave as-is
            return run

        # Keep 1 space at each end
        inner_len = run_len - 2
        repeated = (pattern * ((inner_len // len(pattern)) + 1))[:inner_len]

        return " " + repeated + " "

    return re.sub(r' +', repl, text)
async def get_sender_str(channel: RFChannel, sender_username: str):
    async with get_async_session() as session:
        sender = await get_formatted_sender_data(session, channel.channel_id, sender_username)
    return sender

async def send_ravenfall(channel: RFChannel, message: dict, timeout: int = 15):
    def check(msg: RavenfallMessage):
        return msg.CorrelationId == message.get('CorrelationId', None)
    task1 = asyncio.create_task(channel.ravenfall_waiter.wait_for_message(check, timeout))
    req_response = await send_to_server(channel.middleman_connection_id, json.dumps(message))
    if not req_response["success"]:
        logger.error(f"Could not talk to Ravenfall: {req_response}")
        raise CouldNotSendMessageError("Could not talk to Ravenfall")
    result = await task1
    if result is None:
        raise TimeoutError("Timed out waiting for response")
    response_dict = result

    match = channel.rfloc.identify_string(response_dict['Format'])
    formatted_response = channel.rfloc.translate_string(response_dict['Format'], response_dict['Args'], match).strip()
    edited_response = response_dict.copy()
    edited_response["CorrelationId"] = None
    edited_response["Format"] = formatted_response
    edited_response["Args"] = []

    response_id = None
    if match is not None:
        response_id = match.key
    return RavenfallResponse(
        response = response_dict,
        response_id = response_id,
        formatted_response = formatted_response
    )

async def wait_for_message(channel: RFChannel, check, timeout: int = 15):
    response = await channel.ravenfall_waiter.wait_for_message(check, timeout)
    if response is None:
        raise TimeoutError("Timed out waiting for response")
    response_dict = response
    match = channel.rfloc.identify_string(response_dict['Format'])
    formatted_response = channel.rfloc.translate_string(response_dict['Format'], response_dict['Args'], match).strip()
    edited_response = response_dict.copy()
    edited_response["CorrelationId"] = None
    edited_response["Format"] = formatted_response
    edited_response["Args"] = []

    response_id = None
    if match is not None:
        response_id = match.key
    return RavenfallResponse(
        response = response_dict,
        response_id = response_id,
        formatted_response = formatted_response
    )

async def get_coins_count(channel: RFChannel):
    char_coins = await get_char_coins(channel.channel_id)
    total_coins = 0
    for user in char_coins["data"]:
        if user["coins"] <= 0:
            continue
        total_coins += user["coins"]
    return total_coins

def get_item(item_name: str) -> Item:
    item_search_results = ravenpy.search_item(item_name, limit=1)
    if not item_search_results:
        return None
    if item_search_results[0][1] < 85:
        return None
    return item_search_results[0][0]

async def get_item_count(channel: RFChannel, item_name: str) -> Tuple[Item, int]:
    item = get_item(item_name)
    if item is None:
        return None, 0
    char_items = await get_char_items(channel.channel_id)
    total_items = 0
    for user in char_items["data"]:
        for user_item in user["items"]:
            if user_item['equipped']:
                continue
            if user_item["id"] == item.id:
                total_items += user_item["amount"]
                break
    return item, total_items

async def get_all_item_count(channel: RFChannel) -> Dict[str, int]:
    char_items = await get_char_items(channel.channel_id)
    total_items = {}
    for user in char_items["data"]:
        for user_item in user["items"]:
            if user_item['equipped']:
                continue
            item = ravenpy._items_id_data.get(user_item["id"])
            if item is None:
                continue
            if item.name in total_items:
                total_items[item.name] += user_item["amount"]
            else:
                total_items[item.name] = user_item["amount"]
    return total_items

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
            raise OutOfItemsError("Not enough stock")

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
                    return_dict = True,
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
            raise PartialSendError(f"Ran out of coins ({coins_remaining} remaining)", total_coins - coins_remaining)

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
            raise OutOfItemsError("Not enough stock")

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
            
            send_exception = None
            def no_recipient_check(msg: RavenfallMessage):
                format_match = msg['Format'] == "Could not find an item or player matching the query '{query}'"
                username_match = msg["Args"][0] == f"{target_user_name} {item.name} {items_to_send}"
                return format_match and username_match
            task1 = asyncio.create_task(wait_for_message(channel, no_recipient_check, timeout=10))
            task2 = asyncio.create_task(send_ravenfall(
                channel, RavenBotTemplates.gift_item(
                    sender = await get_sender_str(channel, user_item["user_name"]),
                    recipient_user_name = target_user_name,
                    item_name = item.name,
                    item_count = items_to_send,
                    return_dict = True,
                )
            ))
            try:
                done, pending = await asyncio.wait(
                    [task1, task2],
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=15
                )
                response = done.pop().result()
                for p in pending:
                    p.cancel()
            except Exception as e:
                response = None
                send_exception = e

            if send_exception is not None:
                logger.info(f"Failed to send {item.name} to {target_user_name} from {user_item['user_name']}: {send_exception}")
                if not one_item_successful:
                    raise send_exception

            if response.response_id not in ("gift", "gift_item_not_owned"):
                logger.info(f"Failed to send {item.name} to {target_user_name} from {user_item['user_name']}: {response.response_id}")
                if response.response_id == "gift_player_not_found":
                    raise RecipientNotFoundError("Recipient is not in the game")
                if not one_item_successful:
                    raise CouldNotSendItemsError("Failed to send items")

            items_sent = 0
            if response is not None:
                if response.response_id == "gift_item_not_owned":
                    items_sent = 0
                elif response.response_id == "gift":
                    items_sent = int(response.response["Args"][0])
                else:
                    items_sent = 0
            items_remaining -= items_sent
            one_item_successful = True

        if amount != -1 and items_remaining > 0:
            raise PartialSendError(f"Ran out of items ({items_remaining} remaining)", total_items - items_remaining)


class RedeemRFCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
        self.item_price_dict = {}
        item_values_path = os.getenv("ITEM_VALUES_PATH", "data/item_values.json")
        if os.path.exists(item_values_path):
            item_price_json = json.load(open(item_values_path))
            self.item_price_dict = {item_name.lower(): price for item_name, price in item_price_json.items()}
        else:
            logger.error("item_values.json not found")
        self.idle_points.start()

    @routine(delta=timedelta(seconds=15), wait_remainder=True, max_attempts=99999)
    async def idle_points(self):
        for ch in self.rf_manager.channels:
            chars: List[Player] = await ch.get_query("select * from players")
            if not chars:
                continue

            now = datetime.now(timezone.utc)
            char_ids = [player.get("id") for player in chars if player.get("id")]
            if not char_ids:
                continue

            async with get_async_session() as session:
                char_rows = await session.execute(
                    select(Character).where(Character.id.in_(char_ids))
                )
                characters = {char.id: char for char in char_rows.scalars()}

                idle_rows = await session.execute(
                    select(UserCreditIdleEarn).where(UserCreditIdleEarn.char_id.in_(char_ids))
                )
                channel_db = await get_channel(session, id=ch.channel_id, name=ch.channel_name)
                earn_rate = 3 if not channel_db else channel_db.idle_earn_rate

                idle_records = {rec.char_id: rec for rec in idle_rows.scalars()}

                present_ids = set()

                for player in chars:
                    char_id = player.get("id")
                    if not char_id:
                        continue
                    present_ids.add(char_id)

                    character = characters.get(char_id)
                    if character is None:
                        continue

                    record = idle_records.get(char_id)
                    if record is None:
                        record = UserCreditIdleEarn(
                            char_id=char_id,
                            total_time=0,
                            last_seen_timestamp=now,
                        )
                        session.add(record)
                        idle_records[char_id] = record
                        prev_total = 0.0
                        last_seen = None
                    else:
                        prev_total = float(record.total_time or 0)
                        last_seen: datetime = record.last_seen_timestamp
                        if last_seen is not None and last_seen.tzinfo is None:
                            last_seen = last_seen.replace(tzinfo=timezone.utc)

                    elapsed = 0.0
                    if last_seen is not None:
                        elapsed = (now - last_seen).total_seconds()
                        if elapsed < 0:
                            elapsed = 0.0
                        if elapsed > 40:  # treat as a fresh re-entry
                            elapsed = 0.0

                    record.total_time = prev_total + elapsed
                    record.last_seen_timestamp = now

                    interval_seconds = channel_db.idle_earn_interval

                    earned_chunks = (
                        int(record.total_time // interval_seconds)
                        - int(prev_total // interval_seconds)
                    )
                    if earned_chunks > 0 and character.twitch_id is not None:
                        credits = earned_chunks * earn_rate
                        await add_credits(
                            session,
                            character.twitch_id,
                            credits,
                            f"Idle town earnings ({character.id})",
                        )

    async def send_coins_redeem(self, ctx: RedeemContext, amount: int):
        channel = self.rf_manager.get_channel(channel_id=ctx.redemption.broadcaster_user_id)
        if channel is None:
            return
        await ctx.send(f"Sending {amount:,} coins to {ctx.redemption.user_login}...")
        try:
            await send_coins(ctx.redemption.user_login, channel, amount)
        except OutOfItemsError as e:
            await ctx.cancel()
            logger.error(f"Error in coin redeem: {e}")
            await ctx.send(f"There are not enough coins in stock. You have been refunded.")
            return
        except (CouldNotSendMessageError, CouldNotSendItemsError, TimeoutError) as e:
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
        await ctx.fulfill()
    
    @Cog.redeem(name="Recieve 25,000 coins")
    async def coins_25_000(self, ctx: RedeemContext):
        await self.send_coins_redeem(ctx, 25000)

    @Cog.redeem(name="Recieve 250,000 coins")
    async def coins_250_000(self, ctx: RedeemContext):
        await self.send_coins_redeem(ctx, 250000)

    @Cog.redeem(name="Recieve 1,000,000 coins")
    async def coins_1_000_000(self, ctx: RedeemContext):
        await self.send_coins_redeem(ctx, 1000000)

    async def send_item_credits_redeem(self, ctx: RedeemContext, amount: int, quiet: bool = False):
        async with get_async_session() as session:
            trans_id = await add_credits(session, ctx.redemption.user_id, amount, "Item credits redeem")
            if not quiet:
                await ctx.send(f"You have been given {amount:,} item credits. (ID: {trans_id})")
        await ctx.fulfill()

    @Cog.redeem(name="Lurking!")
    async def lurking(self, ctx: RedeemContext):
        await ctx.send("Thanks for lurking!")
        await self.send_item_credits_redeem(ctx, ctx.redemption.reward.cost, quiet=True)

    @Cog.redeem(name="Get 100 item credits")
    async def item_credits_100(self, ctx: RedeemContext):
        await self.send_item_credits_redeem(ctx, 100)

    @Cog.redeem(name="Get 500 item credits")
    async def item_credits_500(self, ctx: RedeemContext):
        await self.send_item_credits_redeem(ctx, 500)

    @Cog.redeem(name="Get 3,000 item credits")
    async def item_credits_3000(self, ctx: RedeemContext):
        await self.send_item_credits_redeem(ctx, 3000)

    @Cog.redeem(name="Get 15,000 item credits")
    async def item_credits_15_000(self, ctx: RedeemContext):
        await self.send_item_credits_redeem(ctx, 15000)

    @Cog.command(
        name="credits balance",
        aliases=[
            "credits bal",
            "credits b",
            "creditsbal",
            "creditsb",
            "credits",
            "credit bal",
            "credit b",
            "creditbal",
            "creditb",
            "credit",
        ]
    )
    async def credits_balance(self, ctx: CommandContext):
        async with get_async_session() as session:
            credits = await get_user_credits(session, ctx.msg.user.id)
            await ctx.reply(f"You have {credits:,} item {pl(credits, 'credit', 'credits')}.")

    @Cog.command(
        name="credits value",
        aliases=[
            "credits val",
            "credits v",
            "creditsval",
            "creditsv",
            "credit value",
            "credit val",
            "credit v",
            "creditval",
            "creditv",
        ]
    )
    async def credits_value(self, ctx: CommandContext):
        item_name = ctx.parameter
        if not item_name:
            await ctx.reply(f"Usage: !{ctx.command} <item>")
            return
        item = get_item(item_name)
        if item is None:
            await ctx.reply("Item not found")
            return
        if item.soulbound:
            await ctx.reply(f"{item.name} is soulbound and cannot be redeemed.")
            return
        price = self.item_price_dict.get(item_name.lower(), 0)
        if price == 0:
            await ctx.reply(f"{item.name} is not redeemable.")
            return
        await ctx.reply(f"{item.name} is worth {price:,} item {pl(price, 'credit', 'credits')}.")

    @Cog.command(
        name="credits buy",
        aliases=[
            "creditsbuy",
            "credits redeem",
            "creditsredeem",
            "credits purchase",
            "creditspurchase",
            "credit buy",
            "creditbuy",
            "credit redeem",
            "creditredeem",
            "credit purchase",
            "creditpurchase",
        ]
    )
    async def credits_buy(self, ctx: CommandContext):
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        args = ctx.parameter.split()

        if len(args) < 1:
            await ctx.reply(f"Usage: !{ctx.command} <item> [count]")
            return

        count = 1
        if args[-1].isdigit():
            count = int(args.pop())
        item_name = " ".join(args)

        item = get_item(item_name)
        if item is None:
            await ctx.reply(f"Could not identify item. Please check your spelling.")
            return
        if item.soulbound:
            await ctx.reply(f"{item.name} is soulbound and cannot be redeemed.")
            return
        price = self.item_price_dict.get(item_name.lower(), 0)
        if price == 0:
            await ctx.reply(f"{item.name} is not redeemable.")
            return
        async with get_async_session() as session:
            balance = await get_user_credits(session, ctx.msg.user.id)
            if balance < price * count:
                await ctx.reply(
                    f"You do not have enough credits to purchase {count:,}× {item.name}{pl(count, '', '(s)')}. "
                    f"You have {balance:,} {pl(balance, 'credit', 'credits')}. "
                    f"You need {price * count:,} {pl(price * count, 'credit', 'credits')}.")
                return

        try:
            await send_items(ctx.msg.user.name, channel, item.name, count)
        except OutOfItemsError as e:
            logger.error(f"Error in item redeem: {e}")
            await ctx.send(
                f"There are not enough {count:,}× {item.name}{pl(count, '', '(s)')} in stock. "
                "Your credits were not deducted."
            )
            return
        except PartialSendError as e:
            async with get_async_session() as session:
                trans_id = await add_credits(session, ctx.msg.user.id, -price * e.items_sent, f"Shop purchase: {item.name} x{count}")
            await ctx.send(
                f"There were not enough {count:,}× {item.name}{pl(count, '', '(s)')} in stock. "
                f"You received {e.items_sent:,}× {item.name}{pl(e.items_sent, '', '(s)')}. "
                f"(ID: {trans_id})"
            )
            return
        except (CouldNotSendMessageError, CouldNotSendItemsError, TimeoutError, ItemNotFoundError, RecipientNotFoundError) as e:
            logger.error(f"Error in command: {e}")
            await ctx.send(f"❌ Error: {e}. Your credits were not deducted.")
            return
        except Exception as e:
            logger.error(f"Unknown error occured in command: {e}")
            await ctx.send(f"❌ An unknown error occured. Please try again later. Your credits were not deducted.")
            return

        async with get_async_session() as session:
            trans_id = await add_credits(session, ctx.msg.user.id, -price * count, f"Shop purchase: {item.name} x{count}")
            balance -= price * count

        await asyncio.sleep(0.5)
        await ctx.reply(
            f"You have been given {count:,}× {item.name}{pl(count, '', '(s)')}. "
            f"(ID: {trans_id})"
        )


    @Cog.command(
        name="stock coins",
        aliases=[
            "stockcoins",
            "stock coin",
            "stockcoin",
            "credits stock coins",
            "credits stock coin",
            "creditsstockcoins",
            "creditsstockcoin",
            "credit stock coins",
            "credit stock coin",
            "creditstockcoins",
            "creditstockcoin",
        ]
    )
    async def stock_coins(self, ctx: CommandContext):
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        count = await get_coins_count(channel)
        await ctx.reply(
            f"There {pl(count, 'is', 'are')} currently {count:,} {pl(count, 'coin', 'coins')} in stock."
        )

    @Cog.command(
        name="stock",
        aliases=[
            "credits stock",
            "creditsstock",
            "credit stock",
            "creditstock",
        ]
    )
    async def stock_item(self, ctx: CommandContext):
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        if len(ctx.parameter.strip()) == 0:
            await ctx.reply(f"Usage: !{ctx.command} <item>")
            return
        item, count = await get_item_count(channel, ctx.parameter)
        if item is None:
            await ctx.reply(f"Could not identify item.")
            return
        warning = ""
        if item.soulbound:
            warning = " (This item cannot be redeemed.)"
        await ctx.reply(
            f"There {pl(count, 'is', 'are')} currently {count:,}× {item.name}{pl(count, '', '(s)')} in stock.{warning}"
        )
    
    @Cog.command(name="stock all")
    async def stock_all(self, ctx: CommandContext):
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        item_counts = await get_all_item_count(channel)
        out_str = [
            "Stock list for channel: " + channel.channel_name,
            ""
        ]
        categories = {
            "Raw Materials": [],
            "Materials": [],
            "Armor": [],
            "Weapons": [],
            "Accessories": [],
            "Pets": [],
            "Food": [],
            "Potions": [],
            "Cosmetics": [],
            "Scrolls": [],
            "Other": [],
        }
        for item in ravenpy.get_all_items():
            if item.name not in item_counts:
                item_counts[item.name] = 0
        item_counts_list = sorted(list(item_counts.items()), key=lambda x: x[0])
        item_counts_list = sorted(item_counts_list, key=lambda x: getattr(ravenpy._items_name_data[x[0]].material, 'value', 0))
        item_counts_list = sorted(item_counts_list, key=lambda x: x[1] > 0, reverse=True)
        item_cols = 27
        num_cols = 6
        for item_name, count in item_counts_list:
            item = ravenpy._items_name_data[item_name]
            count = 0
            if item.name in item_counts:
                count = item_counts[item.name]
            warning = ""
            if item.soulbound:
                warning = " (Cannot be redeemed.)"
            item_str = f"{item.name.ljust(item_cols)} {str(count).rjust(max(0, min(num_cols, (item_cols+6) - len(item.name) )))}"
            item_str = fill_whitespace(item_str, ".")
            item_str = f"  {item_str}{warning}"
            if item.category == ravenpy.ItemCategory.Resource and len(item.used_in) > 0:
                if not item.craft_ingredients:
                    categories["Raw Materials"].append(item_str)
                else:
                    categories["Materials"].append(item_str)
            else:
                match item.category:
                    case ravenpy.ItemCategory.Armor:
                        categories["Armor"].append(item_str)
                    case ravenpy.ItemCategory.Weapon:
                        categories["Weapons"].append(item_str)
                    case ravenpy.ItemCategory.Ring | ravenpy.ItemCategory.Amulet:
                        categories["Accessories"].append(item_str)
                    case ravenpy.ItemCategory.Pet:
                        categories["Pets"].append(item_str)
                    case ravenpy.ItemCategory.Food:
                        categories["Food"].append(item_str)
                    case ravenpy.ItemCategory.Potion:
                        categories["Potions"].append(item_str)
                    case ravenpy.ItemCategory.Cosmetic | ravenpy.ItemCategory.Skin:
                        categories["Cosmetics"].append(item_str)
                    case ravenpy.ItemCategory.Scroll:
                        categories["Scrolls"].append(item_str)
                    case _:
                        categories["Other"].append(item_str)
        for category_name, items in categories.items():
            if not items:
                continue
            out_str.append(f"{category_name} --- -- -- - -")
            out_str.extend(items)
            out_str.append("")        
        url = await upload_to_pastes("\n".join(out_str))
        await ctx.reply(f"Stock list: {url}")

    @Cog.command(name="giftto")
    async def giftto(self, ctx: CommandContext):
        if os.getenv("OWNER_TWITCH_ID") != ctx.msg.user.id:
            return

        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        args = ctx.parameter.split()

        if len(args) < 2:
            await ctx.reply(f"Usage: !{ctx.command} <user> <item> [count]")
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
        except OutOfItemsError as e:
            logger.error(f"Error in item redeem: {e}")
            await ctx.send(f"There are not enough items in stock.")
            return
        except (CouldNotSendMessageError, CouldNotSendItemsError, TimeoutError, ItemNotFoundError, PartialSendError, RecipientNotFoundError) as e:
            logger.error(f"Error in command: {e}")
            await ctx.send(f"❌ Error: {e}")
            return
        except Exception as e:
            logger.error(f"Unknown error occured in command: {e}")
            await ctx.send(f"❌ An unknown error occured. Please try again later.")
            return
            
        await asyncio.sleep(0.5)
        await ctx.reply("Okay")


    @Cog.command(
        name="addcredits",
        aliases=[
            "addcredit",
            "add credit",
            "add credits",
        ]
    )
    async def addcredits(self, ctx: CommandContext):
        if os.getenv("OWNER_TWITCH_ID") != ctx.msg.user.id:
            return

        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        args = ctx.parameter.split()

        if len(args) < 2:
            await ctx.reply(f"Usage: !{ctx.command} <user> <amount>")
            return

        amount = None
        try:
            amount = int(args.pop())
        except ValueError:
            await ctx.reply(f"Usage: !{ctx.command} <user> <amount>")
            return

        recipient_name = args[0]
        user = await first(self.rf_manager.chat.twitch.get_users(logins=[recipient_name]))
        if user is None:
            await ctx.reply(f"User {recipient_name} not found")
            return
        
        async with get_async_session() as session:
            trans_id = await add_credits(session, user.id, amount, f"Added by {ctx.msg.user.name}")
            await ctx.reply(f"Gave {amount:,} {pl(amount, 'credit', 'credits')} to {recipient_name}. (ID: {trans_id})")


    @Cog.redeem(name="Restart Ravenfall")
    async def restart_ravenfall(self, ctx: RedeemContext):
        channel = self.rf_manager.get_channel(channel_id=ctx.redemption.broadcaster_user_id)
        if channel is None:
            return

        task = channel.queue_restart(30, reason=RestartReason.USER)
        if task:
            await ctx.send("Ravenfall will restart in 30 seconds!") 
            await ctx.fulfill()
        else:
            await ctx.send("A restart is already scheduled!")
            await ctx.cancel()
            
    @Cog.redeem(name="Restart RavenBot")
    async def restart_ravenbot(self, ctx: RedeemContext):
        channel = self.rf_manager.get_channel(channel_id=ctx.redemption.broadcaster_user_id)
        if channel is None:
            return

        await ctx.send("Restarting RavenBot...")
        await channel.restart_ravenbot()
        await ctx.send("Done!")


def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(RedeemRFCog, rf_manager=rf_manager, **kwargs)