def on_twitch_redeem(match_fn: Callable[[TwitchRedemptionEvent], bool]):
    return lambda_filter_decorator(
        [TwitchRedemptionEvent], match_fn, dispatcher_type=Dispatcher.TwitchRedeem
    )
