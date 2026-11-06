# ravenfall-channelbot

Twitch bot that runs Ravenfall towns: it watches each town's RavenBot and game
process, restarts them when they stall, updates the game, translates and
filters game messages through the middleman, and runs a credit economy and a
scroll queue. Python 3.13, asyncio, SQLAlchemy async over `data.db`.

## Running the tests

From the repo root, in the background or with a timeout of at least 400000 ms
(the suite takes about 4 minutes):

```bash
MULTICHAT_COMMAND_SERVER_HOST=127.0.0.1 MULTICHAT_COMMAND_SERVER_PORT=7200 RF_MIDDLEMAN_HOST=127.0.0.1 RF_MIDDLEMAN_PORT=7101 PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m unittest discover -s tests -p "test_*.py"
```

Use `-s tests` without `-t .`: `tests` is not a package. The env vars are
required because `bot/multichat_command.py` and `bot/middleman.py` read them at
import. In a worktree with no `.venv`, add
`UV_PROJECT_ENVIRONMENT="../../../.venv"` to use the main checkout's.

## Where things live

| Path | What |
| --- | --- |
| `main.py` | Entry point: logging, Twitch setup, cog registration, `run()` |
| `bot/ravenfallmanager.py` | `RFChannelManager`: all towns, global routines, processor routing, memory and item monitors |
| `bot/ravenfallchannel.py` | `RFChannel`: one town. Message handling, watchdog, restarts, scroll queue, auto raids |
| `bot/ravenfallrestarttask.py` | `RFRestartTask`: countdown, event pausing, `RestartReason` |
| `bot/ravenfallupdater.py` | `RFGameUpdater`: game and RavenBot updates |
| `bot/ravenfallshutdown.py` | `RFTownShutdown`: stop and start all towns |
| `bot/messageprocessor.py` | Websocket server the middleman forwards each message to |
| `bot/middleman.py`, `bot/multichat_command.py` | HTTP clients for the middleman and ravenfall-multichat |
| `bot/ravenfallloc.py`, `data/definitions.yaml` | Game string identification and translation |
| `bot/commands/` | Command framework: events, dispatchers, listeners, decorators, checks, converters |
| `bot/cogs/` | Commands and redeems. `redeem_rf.py` holds credits and the scroll commands, `game.py` the admin commands |
| `database/` | Tables in `models.py`, helpers in `utils.py`, session in `session.py` |
| `ravenpy/` | RavenNest API client, shared with `ravenhelper-bot` |
| `utils/` | Sandboxie process control (`runshell.py`), routines, alert monitors, helpers |
| `tests/` | `test_channel_bugfixes.py`, `test_command_framework.py`, `test_ravenfall_updater.py`, `test_ravenfall_shutdown.py`, `test_ravenfall_loc.py` |
| `script_*.py` | One-off scripts run by hand |

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
- Don't throttle `bot/middleman.py`. RavenBot's chat limit is 20 messages per
  30 seconds per channel, and the user chose not to work around it.
- `ravenpy/` is shared with `ravenhelper-bot`, so don't change it casually.
- Leave the vendored `twitchAPI/` and `utils/routines.py` alone.
- Commit messages are short and lowercase and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Gotchas

- Local `.env` and `config.toml` belong to the rewrite. `main` reads older
  variables (see `.env.example`), so importing `bot.ravenfallchannel` needs env
  vars set, and `main.py` needs `channels.json`.
- Live towns run on `pc3-server` (`rf_query_url`s, middleman `pc3-server:7101`,
  multichat `pc3-server:7200`), not this PC. Local logs and game files say nothing
  about live behaviour.
- `pc3-server` runs an old middleman build, and moving `main` to the newest one is
  in progress, so `bot/messageprocessor.py` accepts both key styles
  (`correlation_id` and `correlationId`). Keep both until that's finished. The HEAD
  of the middleman repo isn't what's live.
- The local game source is stale. Use upstream `zerratar/Ravenfall-Legacy`.
- `bot/ravenfallchannel.py` is very large and due to be split. Search it rather
  than reading it whole.
- Middleman "client" means RavenBot and "server" means Ravenfall.
- RavenBot lost replies come from its chat limit, not the game. To probe the
  middleman use `player_count`: `ping` doesn't come back through
  `/api/send-and-wait-response`.
- The game message field is spelled `Recipent` on the wire.
- RavenWeave (`RavenWeave.exe`, `updater/`) was removed on purpose. Don't let
  updates install it.
