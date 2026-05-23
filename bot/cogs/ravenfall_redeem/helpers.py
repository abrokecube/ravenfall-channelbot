from __future__ import annotations

import asyncio
import logging
import random
import re
from collections import defaultdict
from typing import TYPE_CHECKING

from bot.cogs.accounts.service import AccountService
from bot.db.session import get_async_session
from bot.integrations.ravenfall import RavenfallMessageEvent, payloads
from bot.integrations.ravenfall.models import RavenfallFormattedMessage
from bot.integrations.twitch import EVENT_SOURCE_TWITCH
from bot.services.event_waiter import EventWaiterService
from bot.services.ravenfall_channels import RavenfallChannelService
from bot.services.ravenfall_multichat import RavenfallMultichatService
from ravenpy import ravenpy

from . import exceptions

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from bot.clients.ravenfall_middleman import Sender
    from bot.core.components import BaseEvent, GlobalContext
    from bot.integrations.ravenfall import RavenfallInstance

LOGGER = logging.getLogger(__name__)


def fill_whitespace(text: str, pattern: str = ". "):
    """Replace whitespace runs with a repeated pattern, keeping a single real space
    at each edge of the run. The total length of the run is preserved.

    Example:
        "a          b" -> "a . . . .  b"
    """

    def repl(m: re.Match[str]):
        run = m.group(0)
        run_len = len(run)
        _something = 2
        if run_len <= _something:
            # Too short to fit pattern inside — leave as-is
            return run

        # Keep 1 space at each end
        inner_len = run_len - _something
        repeated = (pattern * ((inner_len // len(pattern)) + 1))[:inner_len]

        return " " + repeated + " "

    return re.sub(r" +", repl, text)


async def send_to_ravenfall(
    instance: RavenfallInstance,
    sender: Sender,
    payload: payloads.BaseRavenBotPayload,
    timeout: int = 2,
):
    """Send a message to Ravenfall."""
    response = await instance.send_to_ravenfall_and_wait_for_response(
        sender, payload, timeout
    )
    if not response.success:
        msg = "Could not talk to Ravenfall"
        raise exceptions.CouldNotSendMessageError(msg)
    if response.timeout:
        msg = "Timed out waiting for response."
        raise TimeoutError(msg)
    if not response.responses:
        msg = "Did not get a response from Ravenfall"
        raise exceptions.UnexpectedResponseError(msg)
    response_object = response.responses[0]
    if not isinstance(response_object, RavenfallFormattedMessage):
        msg = "Received an unexpected response from Ravenfall"
        raise exceptions.UnexpectedResponseError(msg)
    return response_object


async def wait_for_ravenfall_message(
    instance: RavenfallInstance,
    check: Callable[[RavenfallMessageEvent], bool],
    g_ctx: GlobalContext,
    timeout: float = 15,
    seconds_before: float = 0.1,
):
    """Wait for a Ravenfall message matching 'check'."""
    waiter_srv = g_ctx.require_service(EventWaiterService)

    def predicate(event: BaseEvent):
        if not isinstance(event, RavenfallMessageEvent):
            return False
        if event.ravenfall != instance:
            return False
        return check(event)

    return await waiter_srv.wait_for(
        RavenfallMessageEvent,
        predicate=predicate,
        timeout=timeout,
        seconds_before=seconds_before,
    )


def get_item(item_name: str) -> ravenpy.Item | None:
    """Return a Ravenfall Item matching `item_name` or None if not found/confident."""
    item_search_results = ravenpy.search_item(item_name, limit=1)
    if not item_search_results:
        return None
    _threshold = 85
    if item_search_results[0][1] < _threshold:
        return None
    return item_search_results[0][0]


async def get_coins_count(channel_id: str, g_ctx: GlobalContext) -> int:
    """Return total coins available across characters in `channel`."""
    multichat_srv = g_ctx.require_service(RavenfallMultichatService)
    multichat_client = multichat_srv.get_client()
    char_coins = await multichat_client.get_char_coins(channel_id)
    total_coins = 0
    for user in char_coins:
        if user.coins <= 0:
            continue
        total_coins += user.coins
    return total_coins


async def get_item_count(
    channel_id: str, item_name: str, g_ctx: GlobalContext
) -> tuple[ravenpy.Item | None, int]:
    """Return (Item, total_count) of non-equipped items available in `channel`."""
    multichat_srv = g_ctx.require_service(RavenfallMultichatService)
    multichat_client = multichat_srv.get_client()

    item = ravenpy.get_item(item_name)
    if item is None:
        return None, 0
    char_items = await multichat_client.get_char_items(channel_id)
    total_items = 0
    for user in char_items:
        for user_item in user.items:
            if user_item.equipped:
                continue
            if user_item.id == item.id:
                total_items += user_item.amount
                break
    return item, total_items


async def get_all_item_count(channel_id: str, g_ctx: GlobalContext) -> dict[str, int]:
    """Return mapping of item name to total count available in `channel`."""
    multichat_srv = g_ctx.require_service(RavenfallMultichatService)
    multichat_client = multichat_srv.get_client()
    char_items = await multichat_client.get_char_items(channel_id)
    total_items: dict[str, int] = defaultdict[str, int](int)
    for user in char_items:
        for user_item in user.items:
            if user_item.equipped:
                continue
            total_items[user_item.id] += user_item.amount
    final_items: dict[str, int] = {}
    for item, amount in total_items.items():
        matched_item = ravenpy.get_item(item)
        if not matched_item:
            continue
        final_items[matched_item.name] = amount
    return final_items


channel_item_gift_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def get_twitch_username(
    session: AsyncSession, username: str, platform: str, g_ctx: GlobalContext
):
    """Get the Twitch username of a user."""
    account_srv = g_ctx.require_service(AccountService)
    if platform != EVENT_SOURCE_TWITCH:
        a = await account_srv.find_link_by_username(session, platform, username)
        if not a:
            a = await account_srv.find_link_by_display_name(session, platform, username)
        if a:
            b = await account_srv.get_primary_account_link_for_platform(
                session, a.account_id, EVENT_SOURCE_TWITCH
            )
            if b is not None:
                username = b.username
    return username


async def send_items(
    username: str,
    platform: str,
    rf_instance: RavenfallInstance,
    item_name: str,
    amount: int,
    g_ctx: GlobalContext,
    *,
    skip_warmup: bool = False,
):
    """Send coins to `target_user_name` by aggregating coins from other characters.

    Raises:
        OutOfItemsError: If there are not enough coins.
        RecipientNotFoundError: If recipient is not found in-game.
    """
    lock = channel_item_gift_locks[rf_instance.channel_id]
    multichat_srv = g_ctx.require_service(RavenfallMultichatService)
    rf_channel_srv = g_ctx.require_service(RavenfallChannelService)
    multichat_client = multichat_srv.get_client()

    async with get_async_session() as session:
        username = await get_twitch_username(session, username, platform, g_ctx)
    is_coins = item_name.lower() == "coins"

    async with lock:
        username_lower = username.lower()
        item: ravenpy.Item | None = None
        if is_coins:
            char_items: list[tuple[str, str, int, int]] = [
                (x.user_name, x.twitch_id, x.coins, -1)
                for x in await multichat_client.get_char_coins(rf_instance.channel_id)
                if x.coins > 0 and x.user_name.lower() != username_lower
            ]
        else:
            item_search_result = ravenpy.search_item(item_name, 1)
            _min_score = 85
            if not item_search_result:
                raise exceptions.ItemNotFoundError("Item not found")
            if item_search_result[0][1] < _min_score:
                raise exceptions.ItemNotFoundError("Item not found")
            item = item_search_result[0][0]
            item_name = item.name
            char_items = []
            char_items_query = await multichat_client.get_char_items(
                rf_instance.channel_id
            )
            for user in char_items_query:
                char_item = user.items_dict.get(item.id)
                if not char_item:
                    continue
                if char_item.equipped or char_item.soulbound:
                    continue
                if char_item.amount <= 0:
                    continue
                char_items.append(
                    (user.user_name, user.twitch_id, char_item.amount, user.char_index)
                )

        total_items = 0
        random.shuffle(char_items)
        for _, _, sender_amount, _ in char_items:
            total_items += sender_amount

        if total_items < amount:
            raise exceptions.OutOfItemsError("Not enough stock")
        items_remaining = amount
        one_send_successful = skip_warmup

        for sender_name, sender_id, sender_amount, sender_index in char_items:
            if items_remaining <= 0:
                break
            items_to_send = min(items_remaining, sender_amount)
            user_sender = await rf_channel_srv.get_sender_by_twitch_id(
                rf_instance.channel_id, sender_id
            )
            if not user_sender:
                continue
            LOGGER.info(
                f"Sending {items_to_send} {item_name} to {username} from {sender_name}"
            )
            if not one_send_successful:
                try:
                    if is_coins:
                        __ = await send_to_ravenfall(
                            rf_instance, user_sender, payloads.GetCoinCount()
                        )
                    else:
                        __ = await send_to_ravenfall(
                            rf_instance, user_sender, payloads.GetItemCount(item_name)
                        )
                except Exception:
                    LOGGER.exception("Warmup failed")
            send_exception = None

            coro1 = asyncio.create_task(
                send_to_ravenfall(
                    rf_instance,
                    user_sender,
                    payloads.GiftItem(username, item_name, items_to_send),
                )
            )

            def predicate(event: RavenfallMessageEvent, sender_id: str = sender_id):
                id_match = event.message.identifier in {
                    "gift_item_not_owned",
                    "gift_player_not_found",
                }
                return id_match and event.message.recipient.user_id == sender_id

            coro2 = asyncio.create_task(
                wait_for_ravenfall_message(rf_instance, predicate, g_ctx)
            )
            done, pending = await asyncio.wait(
                [coro1, coro2], return_when=asyncio.FIRST_COMPLETED, timeout=15
            )

            try:
                response = done.pop().result()
            except Exception as e:
                LOGGER.exception(
                    f"Failed to send {item_name} to {username} from {sender_name}",
                )
                response = None
                send_exception = e
            for p in pending:
                __ = p.cancel()

            if not isinstance(response, RavenfallFormattedMessage):
                raise exceptions.UnexpectedResponseError(
                    "Received an unexpected response."
                )

            if send_exception is not None:
                if not one_send_successful:
                    raise send_exception
                continue

            if response.identifier not in {
                "gift_coins",
                "gift_coins_one",
                "gift",
                "gift_item_not_owned",
            }:
                LOGGER.warning(
                    f"Failed to send {item_name} to {username} from {sender_name}: "
                    f"({response.identifier}) {response.format_message()}"
                )
                if not one_send_successful:
                    msg = "Failed to send items"
                    raise exceptions.CouldNotSendItemsError(msg)
                items_sent = 0
            elif response.identifier in {
                "gift_player_not_found",
                "gift_fail_target_missing",
            }:
                msg = "Recipient is not in the game"
                raise exceptions.RecipientNotFoundError(msg)
            elif response.identifier == "gift_item_not_owned":
                items_sent = 0
            elif response.identifier == "gift_coins_one":
                items_sent = 1
            else:
                if response.identifier == "gift_coins":
                    item_amount_response = response.format_args.get("amount")
                else:
                    item_amount_response = response.format_args.get("giftCount")
                if not isinstance(item_amount_response, (int, float)):
                    LOGGER.warning(
                        "Received unexpected response: "
                        f"({response.identifier}) {response.format_message()}"
                    )
                    items_sent = 0
                else:
                    items_sent = int(item_amount_response)

            if items_sent > 0:
                try:
                    if is_coins:
                        await multichat_client.track_coin_use(sender_name, items_sent)
                    elif item is not None:
                        await multichat_client.track_item_use(
                            sender_name, sender_index, item.id, items_sent
                        )
                except Exception:
                    LOGGER.exception(
                        f"Could not track use: "
                        f"{sender_name} {sender_index} {items_to_send}x {item_name}"
                    )
                items_remaining -= items_sent
                one_send_successful = True
        if items_remaining > 0:
            msg = f"Ran out of {item_name} ({items_remaining} remaining)"
            raise exceptions.PartialSendError(
                msg,
                total_items - items_remaining,
            )
