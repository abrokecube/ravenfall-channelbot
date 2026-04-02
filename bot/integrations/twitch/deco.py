from typing import Callable
from . import events
from bot.core.decorators import lambda_filter_decorator


def on_twitch_redeem(match_fn: Callable[[events.TwitchRedemptionEvent], bool]):
    return lambda_filter_decorator([events.TwitchRedemptionEvent], match_fn)
