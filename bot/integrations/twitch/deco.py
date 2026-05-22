from collections.abc import Callable

from bot.core.decorators import lambda_filter_decorator
from bot.integrations.twitch.dispatchers import TwitchRedeemDispatcher

from . import events


def on_twitch_redeem(match_fn: Callable[[events.TwitchRedemptionEvent], object | bool]):
    """Decorator for matching Twitch redemption events."""
    return lambda_filter_decorator(
        [events.TwitchRedemptionEvent], match_fn, dispatcher_type=TwitchRedeemDispatcher
    )
