# ravenfall-channelbot

Twitch bot that runs Ravenfall towns: it watches each town's RavenBot and game
process, restarts them when they stall, updates the game, translates and
filters game messages through the middleman, and runs a credit economy and a
scroll queue. Python 3.13, asyncio, SQLAlchemy async over `data.db`.

## Running the tests

From the repo root, with a timeout of at least 180000 ms (the suite takes
about 75 seconds):

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m unittest discover -s tests -p "test_*.py"
```

Use `-s tests` without `-t .`: `tests` is not a package. In a worktree with no
`.venv`, add
`UV_PROJECT_ENVIRONMENT="../../../.venv"` to use the main checkout's.

## Where things live

| Path | What |
| --- | --- |
| `main.py` | Entry point: logging, Twitch setup, cog registration, `run()` |
| `bot/ravenfallmanager.py` | `RFChannelManager`: all towns, global routines, processor routing, memory and item monitors |
| `bot/ravenfallchannel.py` | `RFChannel`: one town. Message handling, watchdog, restarts, scroll queue, auto raids |
| `bot/ravenfallrestarttask.py` | `RFRestartTask`: countdown, event pausing, `RestartReason` |
| `bot/ravenfallupdater.py` | `RFGameUpdater`: game and RavenBot updates |
| `bot/ravenfallshutdown.py` | `RFTownShutdown`: stop and start towns |
| `bot/messageprocessor.py` | Websocket server the middleman forwards each message to |
| `bot/config.py` | `get_settings()` for environment variables, `ChannelConfig` and `load_channels()` for `channels.json` |
| `bot/clients/` | The shared HTTP session (`session.py`) and the game query client (`query.py`) |
| `bot/middleman.py`, `bot/multichat_command.py` | HTTP clients for the middleman and ravenfall-multichat, on the shared session |
| `bot/ravenfallloc.py`, `data/definitions.yaml` | Game string identification and translation |
| `bot/streamquests.py` | Reads stream quest offers and picks the one a town is most likely to finish |
| `bot/commands/` | Command framework: events, dispatchers, listeners, decorators, checks, converters |
| `bot/cogs/` | Commands and redeems. `redeem_rf.py` holds credits and the scroll commands, `exchange.py` the `!exchange` item exchange, `game.py` the admin commands |
| `bot/ravenbotfallback.py` | Posts the game messages RavenBot drops at its chat limit: pending messages from `handle_processor_message`, cleared by RavenBot's chat (`RAVENBOT_USER_ID`) |
| `bot/exchange_log.py` | `bot.exchange` logger: session IDs, saving lines to the database, the 60-day purge |
| `database/` | Tables in `models.py`, helpers in `utils.py`, session in `session.py` |
| `ravenpy/` | RavenNest API client, shared with `ravenhelper-bot` |
| `utils/` | Sandboxie process control (`runshell.py`), routines, alert monitors, helpers |
| `tests/` | `test_channel_bugfixes.py`, `test_clients.py`, `test_command_framework.py`, `test_config.py`, `test_gifting.py`, `test_item_exchange.py`, `test_middleman_api.py`, `test_ravenfall_updater.py`, `test_ravenfall_shutdown.py`, `test_ravenfall_loc.py`, `test_ravenbot_fallback.py`, `test_single_instance.py`, `test_stream_quests.py`. Helpers: `fastclock.py` (sped-up time for tests that run real restarts and updates), `shared_loc.py` (one loaded `definitions.yaml`) |
| `scripts/` | One-off scripts, run from the repo root with `python -m scripts.<name>` (e.g. `python -m scripts.itemcreditprice`) |

Sibling repos in the parent folder: `ravenfall-middleman` (Go), `ravenfall-multichat`,
`ravenfall-webops`, `watchdog`, `ravenhelper-bot`.

## Docs

- [docs/architecture.md](docs/architecture.md): components, startup, how a message
  travels between Twitch, RavenBot, the middleman, the game and the bot, config.
- [docs/restarts-and-monitoring.md](docs/restarts-and-monitoring.md): RavenBot
  watchdog, restart tasks, monitors, updater, town shutdown.
- [docs/commands-and-credits.md](docs/commands-and-credits.md): command framework,
  cogs, credits, scroll queue.

## Rules

- Work on `main` and refactor incrementally. Don't revive the rewrite branches
  (`modularize-compartment` and others). They and the README are stale.
- New work goes in its own session.
- Don't throttle `bot/middleman.py`. RavenBot (LocalBot) sends at most 20 chat
  messages in a 30 second window and throws away the rest without queueing them.
  `bot/ravenbotfallback.py` works around that: it posts from the bot's account
  each game message RavenBot hasn't posted within 1.5 s.
- `ravenpy/` is shared with `ravenhelper-bot`, so don't change it casually.
- Leave the vendored `twitchAPI/` and `utils/routines.py` alone.
- Commit messages are short and lowercase and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Gotchas

- Local `.env` and `config.toml` belong to the rewrite. `main` reads older
  variables (see `.env.example`), and `main.py` needs `channels.json`.
- `get_settings()` is cached after its first call, so `load_dotenv()` must come
  first and nothing should call it at import. A test that changes the environment
  uses `set_env` from `tests/test_channel_bugfixes.py`. `bot/ravenfallupdater.py`
  still reads its own variables with `os.getenv`.
- HTTP calls in `bot/` go through `get_session()` from `bot/clients/session.py`,
  not a new `ClientSession`. It closes each connection after its request and has
  no connection limit, on purpose (see that file).
- The bot runs on several event loops. twitchAPI gives chat and each EventSub
  websocket their own thread and loop, and commands run on those, not the main
  loop. `get_session()` keeps one session per loop for this reason.
- Live towns run on `pc3-server` (`rf_query_url`s, middleman `pc3-server:7101`,
  multichat `pc3-server:7200`), not this PC. Local logs and game files say nothing
  about live behaviour.
- `pc3-server` runs an old middleman build, and moving `main` to the newest one is
  in progress, so `bot/messageprocessor.py` accepts both key styles
  (`correlation_id` and `correlationId`). Keep both until that's finished. The HEAD
  of the middleman repo isn't what's live.
- The `-master` game sources in `C:\Users\borkedcube\sof ware\ravenfall src` are stale. Use the snapshots
  beside them (`Ravenfall-Legacy`, `RavenBot`, `RavenNest`, no git history) or upstream `zerratar/Ravenfall-Legacy`.
- `bot/ravenfallchannel.py` is very large and due to be split. Search it rather
  than reading it whole.
- Middleman "client" means RavenBot and "server" means Ravenfall.
- RavenBot lost replies come from its chat limit, not the game (the bot now resends
  game messages, but not RavenBot's own replies). To probe the
  middleman use `player_count`: `ping` doesn't come back through
  `/api/send-and-wait-response`.
- The game message field is spelled `Recipent` on the wire.
- RavenWeave (`RavenWeave.exe`, `updater/`) was removed on purpose. Don't let
  updates install it.
