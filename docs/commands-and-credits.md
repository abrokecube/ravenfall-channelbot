# Commands and credits

## Command framework

`bot/commands/` is an in-house framework, separate from `twitchAPI`'s command system.

| File | Holds |
| --- | --- |
| `event_manager.py` | `EventManager`: event sources, dispatchers, cogs, `process_event`, `execute_text` |
| `event_sources.py` | `TwitchAPIEventSource`: turns Twitch callbacks into events and assigns roles |
| `events.py` | `MessageEvent`, `CommandEvent`, `TwitchMessageEvent`, `TwitchRedemptionEvent` |
| `dispatchers.py` | `CommandDispatcher`, `TwitchRedeemDispatcher`, `SimpleDispatcher` |
| `listeners.py` | `CommandListener` builds parameters from the signature and docstring |
| `decorators.py` | `command`, `parameter`, `cooldown`, `checks`, `verification`, `on_match`, `on_message`, `on_twitch_redeem` |
| `checks.py` | `MinPermissionLevel`, `HasRole`, `TwitchOnly`, `FunctionCheck` |
| `converters.py` | `RFChannelConverter`, `RFItemConverter`, `TwitchUsername`, `RFSkill`, `Choice`, `RangeInt` and others |
| `cog.py` | `Cog` collects decorated methods; `setup` and `stop` are the lifecycle hooks |
| `global_context.py` | `GlobalContext`: what cogs reach through `self.global_context` |
| `enums.py`, `exceptions.py` | `UserRole`, `BucketType`, and `CommandError` and friends |

`MyCmdDispatcher` in `main.py` subclasses `CommandDispatcher` and takes the prefix
from `BOT_COMMAND_PREFIX` (default `!`). Command names match longest first and may
contain spaces, so `rf restart` works as an alias.

### Writing a command

```python
class MyCog(Cog):
    @command(name='rfrestart', aliases=['rf restart'])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def rfrestart(self, ctx: CommandEvent, seconds: int = 30, *, channel: RFChannel = 'this'):
        """Help text comes from the docstring.

        Args:
            channel: Target channel.
        """
```

- Positional arguments are positional in chat, and keyword-only arguments are flags.
- `RFChannelConverter`'s default of `'this'` resolves to the current room.
- Raise `CommandError` to reply with an error message.
- Roles come from `TwitchAPIEventSource`: `BOT_USER_ID` and `OWNER_TWITCH_ID` are
  `BOT_ADMINISTRATOR`, the broadcaster and lead moderators are `ADMINISTRATOR`, and
  moderators are `MODERATOR`. Redemptions always run as `USER`.
- `TwitchRedeemDispatcher` refunds a redemption automatically if the handler raises.
- Messages are limited to 500 characters. `TwitchMessageEvent` sends over HTTP and
  falls back to IRC.

### Cooldowns

`cooldown` takes one `BucketType` or a list. A list means all of them must match, so
`[BucketType.USER, BucketType.CHANNEL]` is per user in each room. `MessageEvent` and
`CommandEvent` supply the keys through `get_bucket_key`. `GUILD` means the same as
`CHANNEL`, because Twitch has no guilds.

`bot/cooldown.py` and `bot/commands/cooldown.py` both define `Cooldown`. The first
is what `RFChannel` uses.

## Cogs

Added in this order in `main.py`:

| Cog | File | Commands |
| --- | --- | --- |
| `HelpCog` | `bot/cogs/help.py` | Help |
| `ExampleCog` | `bot/cogs/example.py` | Only with `COMMAND_TESTING=1` |
| `TestingCog` | `bot/cogs/testing.py` | Framework test commands |
| `RedeemCog` | `bot/cogs/redeem.py` | `createreward` |
| `RedeemRFCog` | `bot/cogs/redeem_rf.py` | Redeems, credits, stock, scroll queue |
| `ExchangeCog` | `bot/cogs/exchange.py` | `exchange` (craft, uncraft, cancel, pending, log) |
| `GameCog` | `bot/cogs/game.py` | Restarts, `rfupdate`, `rfshutdown`, `rfstart`, scroll admin, backups |
| `InfoCog` | `bot/cogs/info.py` | Towns, events, uptime, system info, character stats |
| `BotStuffCog` | `bot/cogs/bot.py` | `reload_strings`, `pausemon`, `resumemon`, watchdog process control |
| `DebugCog` | `bot/cogs/debug.py` | `debug`, `eval`, `translate` |

`GameCog` also takes `?`-prefixed aliases of its commands, through an `on_message`
listener that calls `execute_text`.

## Credits

The code is in `bot/cogs/redeem_rf.py`, with storage in `database/models.py`
(`UserCredits`, `UserCreditIdleEarn`, `UserCreditTransaction`) and helpers in
`database/utils.py` (`add_credits`, `get_user_credits`).

- **Spending safely**: hold `credit_locks[user_id]` from checking the balance until
  it's deducted, so a user can't spend the same credits twice. `add_credits` is an
  atomic SQL update that returns the transaction id, or -1.
- **Earning**: `idle_points` runs every 15 seconds and uses `idle_earn_rate` and
  `idle_earn_interval` on `Channel`. A gap over 40 seconds counts as rejoining, not
  as time earned. Idle earnings don't record a transaction. Credits also come from
  the `Get N item credits` and `lurking` redeems.
- **Buying items**: `credits buy` goes through `_buy_item`, which deducts only after
  `send_items` succeeds, and a partial send pays only for `items_sent`.
- **Prices**: `ITEM_VALUES_PATH`, by default `data/item_values.json`. A price of 0
  means not redeemable, and soulbound items are refused. `scripts/baseitemvalues.py`
  and `scripts/itemcreditprice.py` generate the price data.
- **Sending stock**: `send_items` and `send_coins` gather stock across the town's
  characters through `get_char_items` and `get_char_coins`, send
  `RavenBotTemplates.gift_item` through `send_ravenfall`, and report it with
  `track_item_use` and `track_coin_use`. `channel_item_gift_locks` serialises this
  per town. With `verify_recipient=True`, `send_items` counts the recipient's items
  (`query_player_item_count`, a `!count` sent as them with `forward=False` so it stays
  out of chat) before each gift, and settles a gift that got no reply by counting
  again instead of treating it as failed. When that can't be settled it raises
  `UnconfirmedSendError`, and nothing should be resent or refunded for it.

Keep the deduct-after-success order for anything else that spends credits.

## Item exchange

`bot/cogs/exchange.py`, logging in `bot/exchange_log.py`.

- `!exchange craft <item> [count]`: the user gifts the recipe's ingredients and gets
  the crafted item. `!exchange uncraft` is the reverse. Only existing, sendable stock is
  given, nothing is crafted. `!exchange <item>` shows the recipe and stock,
  `!exchange cancel` stops and returns what arrived, and `!exchange pending` lists what
  is owed. Admins have `!exchange log <EX-id>` and `!exchange log user <name>`.
- **Fee**: `FEE` (10) credits per command, checked at the start and taken only when
  the user's side has all been delivered or saved for them.
- **Sessions** (`ExchangeRun`) run on the main event loop, where game messages arrive,
  whatever loop the command came in on. `RFChannel.ravenfall_message_listeners` feeds
  them each game message. A gift counts if it's the user's `gift` reply, to any stock
  account in the town (login or display name), for an item still owed.
- **Before the instructions**, the user's inventory is checked with `!count` sent as
  them. These counts are also the baseline for the end, where a gift whose reply was
  lost is counted only if the user's count and the stock both moved.
- **Timer**: `GIFT_WINDOW` (2 minutes) runs only while the user and a stock account
  are in the town and no dungeon is being prepared. `MAX_SESSION` (30 minutes) covers
  everything. Leaving never cancels an exchange.
- **Owed items**: whatever can't be gifted because the user left is saved in
  `pending_exchange_deliveries` and gifted by `deliver_pending` once they're back.
  Sessions still open when the bot stopped are settled by `recover_interrupted` at
  startup. A session that was mid-send is marked `interrupted_sending` for the owner.
- **Logs**: `bot.exchange` writes to `logs/exchange.log`. Lines from a
  `SessionLogAdapter` carry the session ID (`EX-…`, also shown to the user) and are
  saved to `exchange_log_entries` in batches. Sessions older than 60 days are purged
  daily, except ones that still owe something.

## Scroll queue

Commands are in `bot/cogs/redeem_rf.py`; the queue is in `RFChannel`.

`RFChannel.scroll_queue` is a deque of `QueuedScroll` (`bot/models.py`), saved to
`Channel.scroll_queue` as JSON by `save_scroll_queue` and read back by
`load_scroll_queue`.

Entry points: the "Queue Dungeon scroll" and "Queue Raid scroll" redeems, and
`queuescroll`. Limits are the constants at the top of `redeem_rf.py`
(`RAID_SCROLL_SIZE`, `DUNGEON_SCROLL_SIZE`, `MAX_QUEUE_SIZE`,
`NON_MOD_MAX_BATCH_SCROLLS`), and credit costs are set in `_queue_scrolls`. The first
scroll into an empty queue with no event running is free. `add_scroll_to_queue` raises `OutOfStockError` when the queue
already holds all the town's stock.

`scroll_queue_routine` runs every second:

1. Skips while `holding_scrolls` or a shutdown is active, during a restart, when a
   restart is about to start, or during an event.
2. Checks stock with `get_scroll_counts`. A failed check keeps the queue. An empty
   stock removes the entry, refunds it with `_refund_scroll` and announces that.
3. Sends `?rs` or `?ds` through multichat with user id `"0"`, then waits up to 10
   seconds for the event to start.
4. On success, pops the entry and fulfills the redemption. After
   `MAX_SCROLL_QUEUE_FAILURES` failures in a row it refunds the whole queue through
   `remove_scrolls_from_queue(0)`.

`_refund_scroll` cancels the redemption if there is one and returns credits with
`add_credits`.

`!qs refund` calls `RFChannel.refund_user_scrolls`, which removes and refunds every
entry whose `user_id` is the caller. It skips `scroll_in_use`, the head entry the
routine is using at that moment, so the routine's later `popleft` still removes the
correct entry. Free scrolls and channel point redeems have no `user_id`, so this
command never removes them.
