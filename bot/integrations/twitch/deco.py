from typing import Callable

from bot.integrations.twitch.dispatchers import TwitchRedeemDispatcher
from . import events
from bot.core.decorators import lambda_filter_decorator


def on_twitch_redeem(match_fn: Callable[[events.TwitchRedemptionEvent], bool]):
    return lambda_filter_decorator(
        [events.TwitchRedemptionEvent], match_fn, dispatcher_type=TwitchRedeemDispatcher
    )
