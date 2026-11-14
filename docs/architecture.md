# Architecture

## Components

| Component | Where | Role |
| --- | --- | --- |
| Twitch | `twitchAPI/` (vendored) | Chat over IRC, helix calls, EventSub websocket for redeems |
| RavenBot | Separate process, one per Sandboxie box or town | Reads chat commands and forwards them to the game |
| Ravenfall | Game client, one per town, inside a Sandboxie box | The town. Exposes a local HTTP query API |
| Middleman | `ravenfall-middleman` (Go) | Proxy on the RavenBot to game connection. Sends each message to this bot, which can rewrite or block it |
| Multichat | `ravenfall-multichat` | Separate chat bot: `?` commands, item and coin tracking, desync data, per-town character and scroll data |
| WebOps | `ravenfall-webops` | Website actions the API doesn't cover, such as item redemption and loyalty point totals, used through `bot/rf_webops_client.py` |
| Watchdog | `watchdog` | Process watcher used through `bot/process_watchdog_client.py` |
| RavenNest | `ravenpy/` | Game API: login, items, online status |
| This bot | `main.py` | Runs everything above as asyncio tasks in one process |

In the middleman, "client" is RavenBot and "server" is Ravenfall. Message sources
are `client`, `server`, `api-client` and `api-server`, where `api-*` messages were
injected through the middleman's HTTP API and did not come from either process.

## Startup

`main.py` loads `.env`, exits if another copy of the bot is already running on this
machine (`utils/single_instance.py`, a named Windows mutex that covers every checkout
but not other PCs), writes a `pid` file, configures one log file per logger
(`middleman.log`, `rfmsgproc.log`, `rfchannels.log`, `streamquests.log` and so on), and
reads `channels.json` at import. `run()` stops at once if multichat's address isn't
set, then does this in order:

1. `update_schema()` creates missing tables and adds missing columns. It never
   drops or alters anything.
2. Logs in to RavenNest, creates `GlobalContext` and `EventManager`, and adds the
   dispatchers `MyCmdDispatcher` and `TwitchRedeemDispatcher`.
3. Adds the cogs. See [commands-and-credits.md](commands-and-credits.md).
4. `setup_twitch()` authenticates with tokens kept in the database, joins chat, and
   opens one EventSub websocket per channel for channel point redeems.
5. Creates `RFChannelManager` and starts it unless
   `DISABLE_RAVENFALL_INTEGRATION` is set.
6. Starts `SomeEndpoints` (`bot/server.py`), which serves `/metrics` from
   `MetricsManager` (`bot/prom_metrics.py`).

`RFChannelManager.start()` starts each `RFChannel`, its own routines
(`mult_check_routine`, `resync_routine`, `update_boosts_routine`), and
`MessageProcessor`, but the processor only when `RF_MIDDLEMAN_PROCESSOR_HOST` and
`RF_MIDDLEMAN_PROCESSOR_PORT` are set.

`RFChannel.start()` returns early when `monitoring_paused` is set, which the
`pause_monitoring` config key does at startup. That skips every routine and the
scroll queue load, not only the watchdog.

## How a message travels

### Twitch chat to the bot

`Chat` calls `on_message` in `setup_twitch()`, which gathers two handlers
together, so a slow command can't make a town miss the reply it's waiting for:

- `TwitchAPIEventSource.on_message` builds a `TwitchMessageEvent` and hands it to
  `EventManager`, which runs the commands.
- `RFChannelManager.event_twitch_message` passes it to the town's
  `RFChannel.event_twitch_message`. That feeds `twitch_message_waiter`, starts the
  RavenBot watchdog if the text is a monitored command (`monitor_ravenfall_command`),
  and handles the `inspect` command.

RavenBot reads the same chat separately. The bot does not relay chat to it.

### RavenBot to the game

```
Twitch chat -> RavenBot -> middleman -> Ravenfall
                              |
                              v  websocket
                       MessageProcessor -> RFChannelManager.handle_processor_message
                                               |
                                               v
                                       RFChannel.process_ravenbot_message
                                       RFChannel.process_ravenfall_message
```

`ravenfall-middleman/middleman/proxyconnection.go` (`forwardToProcessor`) wraps each
message with its source, addresses, connection id, correlation id and API flag,
then sends it to `MessageProcessor` and waits for the reply.

`handle_processor_message` finds the town by matching `metadata.connection_id`
against `RFChannel.middleman_connection_id`, which comes from the
`middleman_connection_id` channel config key. Then it:

1. Feeds non-API messages to `event_ravenbot_message` or `event_ravenfall_message`,
   which wake `ravenbot_waiter` and `ravenfall_waiter`.
2. Calls `process_ravenbot_message` or `process_ravenfall_message`.
3. Replies with `{"message": ...}` to forward the result, or `{"block": True}` when
   the handler returned `None` or `{'block': True}`.

`process_ravenbot_message` (RavenBot to game) records users and senders, tracks
training for `task` and `leave`, blocks `inspect` because the bot handles it, and
swaps the sender on `observe` and `travel`. An `observe` aimed at a name is also
remembered, so the game's `not_playing` reply to it isn't sent to chat: the game
means the target there, but the text speaks to whoever asked.

`process_ravenfall_message` (game to RavenBot) rounds skill bonuses, identifies the
string with `RavenfallLocalization.identify_string`, ends Ravebridge when configured,
blocks certain system-platform keys, records characters, tracks auto raids, groups
ferry arrivals, turns loot lists into pastes with `utils.utils.upload_to_bin`, and
translates text with `translate_string`, splitting long results across several
messages with `send_split_msgs`.

String keys live in `data/definitions.yaml`. Per-town translations are at the
`ravenfall_loc_strings_path` channel config key, and
`data/example_translation_strings.yaml` shows the format.

### Dropped RavenBot messages

The towns' RavenBot (LocalBot) sends chat through TwitchLib with its default
limit: 20 messages in a 30 second window that starts with the first one. Anything
over that is thrown away, not queued. `bot/ravenbotfallback.py` posts those
messages from the bot's account instead:

1. `handle_processor_message` passes every game message headed for RavenBot (source
   `server` or `api-server`, not blocked) to `RFChannel.expect_ravenbot_chat`. The
   `RavenBotChatFallback` renders it as RavenBot would, with the same template
   rules, the `@user, ` mention, and parts split at 499 characters. Each part is
   kept as pending.
2. `RFChannel.event_twitch_message` passes each chat message from
   `RAVENBOT_USER_ID` to `seen`. That clears the matching pending part, ignoring
   case, spacing and a leading mention. Long texts also match if their first 40
   characters are the same.
3. `ravenbot_fallback_routine` (every 0.25 s) sends whatever is still pending
   1.5 s later. It threads replies to the correlation ID when that is a Twitch
   message ID, otherwise it adds a mention. Parts older than 60 s are let go, and
   so is plain text starting with a command prefix, which RavenBot would run as a
   command.

When RavenBot posts a message the bot already resent, the bot logs it at info
level, which is the sign the delay is too short. The feature is skipped when
`RAVENBOT_USER_ID` isn't set or equals `BOT_USER_ID`, and each town can turn it
off with `resend_dropped_ravenbot_messages`. RavenBot's own replies (help,
cooldowns) never pass the middleman, so they still use up its limit and still
aren't resent.

### The bot to the game and RavenBot

| Route | Used through |
| --- | --- |
| Chat message, read by RavenBot | `RFChannel.send_chat_message`, which also starts the watchdog |
| `?` commands through multichat | `RFChannel.multichat`, which falls back to chat, or `send_multichat_command` |
| Middleman, to the game | `send_to_server`, `send_to_server_and_wait_response`, and `send_ravenfall` in `bot/cogs/redeem_rf.py` |
| Middleman, to RavenBot | `send_to_client`, `RFChannel.send_message_as_ravenbot` |
| Game HTTP query API | `RFChannel.get_query` through `QueryClient` (`bot/clients/query.py`), for `GET {rf_query_url}/select * from session` and similar. Returns `None` on every failure. `RFChannel.get_player` looks up one player. Tables include `session`, `village`, `multiplier`, `dungeon`, `raid`, `players` |

The middleman routes are defined in `ravenfall-middleman/middleman/main.go` and
described in its `API.md`.

HTTP calls in `bot/` share a session from `get_session()` in
`bot/clients/session.py`, which `main.py` closes at shutdown. There's one per
event loop, because twitchAPI runs chat and each EventSub websocket on its own
thread and loop, and commands run on those loops. Each request still
opens its own connection, because the game's query server hasn't been tested with
reused connections, and there's no connection limit, so the middleman isn't
throttled. `utils/utils.py` (pastebins), `utils/websocket_client.py` and `ravenpy/`
keep their own sessions.

## Town state

`update_events_routine` polls the `dungeon` and `raid` queries every second and
sets `event`, `sub_event` and `event_text`. `RFChannelEvent` and `RFChannelSubEvent`
in `bot/models.py` are the values. The restart task and the scroll queue both act on
them.

`update_middleman_connection_status_routine` keeps the town's middleman connection
state current. `RFChannelManager` tracks whether the processor has any connection
(`middleman_connected`) and whether the middleman is in power saving
(`middleman_power_saving`).

## Global routines

| Routine | Every | Does |
| --- | --- | --- |
| `mult_check_routine` | 45 s | Checks RavenNest online status and the global multiplier, restarts on `MULTIPLIER_DESYNC` |
| `resync_routine` | 60 s | Sends `?resync` when multichat reports 30 s or more of desync |
| `update_boosts_routine` | 3 h | Refreshes boosts |

## Database

`database/models.py` has `User`, `Channel`, `Character`, `AutoRaidStatus`,
`SenderData`, `TwitchAuth`, `UserCredits`, `UserCreditIdleEarn`,
`UserCreditTransaction` and `ChatMessage`. The engine is in `database/db.py`, and
`get_async_session` in `database/session.py` commits on exit and rolls back on error.

To add a column, add it to the model. `update_schema()` adds it to existing
databases. Changing or removing a column needs a manual migration.

## Configuration

Secrets and hosts come from environment variables, all listed in `.env.example`.
`main` reads the older names, not those in the local `.env` and `config.toml`.
`get_settings()` in `bot/config.py` reads them into one `Settings` object on its
first call and keeps it. Nothing reads them at import, except that `main.py` calls
it after `load_dotenv()`. `bot/ravenfallupdater.py` still reads its own.

Towns are defined in `channels.json`, ignored by git. `ChannelConfig` in
`bot/config.py` is its schema, with each key's default. Keys it doesn't know are
ignored. The `command_prefix` key is unused: the prefix is the global
`BOT_COMMAND_PREFIX`.
