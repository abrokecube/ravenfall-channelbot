# Refactoring plan for `ravenfall-channelbot`

Refactor `main` in small steps. The live bug fixes are done, so the next job is the shared foundations, followed by splitting up the two biggest files. `bot/ravenfallchannel.py` is a 1,666-line god object, and `bot/cogs/redeem_rf.py` (1,251 lines) is the most-changed file in the repo.

Line references below are from before Phase 0, so search for the names instead of trusting the numbers.

## Status (2026-11-06)
| Phase | State |
|---|---|
| 0: live bugs | Done on `main` (`b7d6d32`), not yet deployed to `pc3-server` |
| 1: foundations | Done on `main`, not yet deployed |
| 2: split `RFChannel` | Next, after the one-player town fixes |
| 3: split the cogs | Not started |
| 4: `main.py` | Not started |
| 5: delete and rename | Dispatcher cleanup, `chatmsg_cd.py` and `scripts/` done, the rest not started |

All 140 tests pass. They now cover the Phase 0 fixes, the command framework and the middleman API, as well as the updater, shutdown and localization.

## Refactor `main`, or revive the rewrite?
`modularize-compartment` already has much of this structure (`bot/clients/`, `cogs/ravenfall_scroll_queue/`). It's 116 commits ahead of `main`, but it has almost no bot work since June and is missing `rfupdate`, `rfstopall`, `RFGameUpdater` and `RFTownShutdown`.

The decision is to refactor `main` and use the rewrite only as a source of parts, especially its `bot/clients/` modules. Merging it would mean redoing the riskiest recent work, which is restart and update handling. The cost is that the rewrite branches will drift further from `main`.

Check anything taken from `bot/clients/` before using it. Its middleman client posts to `/api/send-to-server-and-wait`, which doesn't exist.

## Phase 0: fix live bugs first (done)
All of the planned fixes are on `main` in `b7d6d32`, including chat reaching `RFChannel` again, block replies, refunds, credit races and the `get_query` crashes. `e8ab558` also fixed cooldown buckets and stacked checks in the command framework.

**Still to do on deployment:** chat monitoring, the first-join welcome and `!inspect` are live again after being off since 2026-02-17. Monitoring of viewer commands can restart RavenBot, and lost replies under the chat limit could trigger that, so enable it on one town first.

## Phase 1: shared foundations (medium)
- **Config:** add one typed settings object and a `ChannelConfig` dataclass, keeping the current env var names. Env vars are read in 13 files, and two modules read them at import time, which is why importing `main`'s code fails without them set.
- **HTTP clients:** add query, multichat and middleman clients on a shared session, porting from the rewrite's `bot/clients/`. There are 29 `ClientSession(` call sites today, and every game query opens a new session, with polling several times per second per town. The middleman client must keep working with both the old and new builds until `pc3-server` is switched (see [middleman-upgrade.md](middleman-upgrade.md)).
- **One failure rule for `get_query`:** it now returns `None` on every failure, and Phase 0 fixed the `game.py` callers. Finish this by removing the remaining `except TypeError` in `bot/ravenfallchannel.py`, which depends on the old behaviour.
- **Small helpers:** add `channel.multichat(text)` to replace the repeated send-with-fallback pattern, plus `get_player(name)`, `parse_boost()` and `list_box_pids()`. The PID lookup is currently copied in three places.

## Phase 2: split `RFChannel` (large, one PR per extraction)
`RFChannel` stays as a thin wrapper, so the cogs don't change at first. Lowest risk goes first, and each piece gets tests before it moves:

1. **`ScrollQueue`:** it's self-contained, and refunds already sit in `_refund_scroll`.
2. **`AutoRaidTracker` and training records:** this merges four near-identical "fetch players, then loop over the DB" methods.
3. **`GameStateTracker`:** includes a pure `describe_event()` function that's easy to test.
4. **`MessagePipeline`:** turns the long `if key ==` chain into a table of handlers.
5. **`RavenBotWatchdog`**
6. **`RestartController`:** this goes last because it's the riskiest, and the tests and `RFRestartTask` call private methods directly.

Two cleanups come along with this:
- A `@skip_while_restarting` decorator replaces the repeated lock checks.
- A single `can_restart(reason)` check replaces the conditions in `queue_restart`.

Code outside `RFChannel` also reaches into its locks and flags in 44 places, and those should move to public methods.

## Phase 3: split the cogs (medium)
- **`redeem_rf.py`:** split it into `ItemGiftService`, credits/shop and scroll-queue commands. Merge `send_coins` with `send_items`, and `queue_dungeon` with `queue_raid`.
- **`ScrollKind` table:** one table holds cost, size, stock key and command, replacing the `if dungeon / elif raid` branches.
- **Error messages:** one mapping replaces the repeated `except` chains. Also rename the local `TimeoutError`, which shadows the builtin.
- **`game.py`:** `ds` and `rs` are nearly identical, so they can share a precondition check.

## Phase 4: `main.py` as a real entry point (small)
- Move the import-time side effects (pid file, logging, reading `channels.json`) into an `App` with matching start and stop.
- Remove the star imports that `Optional` and `Dict` currently depend on.
- Fix shutdown: it crashes when `DISABLE_RAVENFALL_INTEGRATION` is set, `update_boosts_routine` is never cancelled, and the HTTP server is never stopped.
- Fix the loop exception handler, which raises `KeyError` when a context has no `exception` key.

## Phase 5: delete and rename (small, any time)
- **Dead code:** old commented-out imports in the cogs, chat routes in `server.py`, `stop_event` in `middleman.py`, `filter_text` and the broken `_func_to_listener` in `dispatchers.py`, `utils/chatmsg_cd.py`, and the never-read `command_prefix`.
- **Duplicates and confusing names:**
  - `strjoin` exists twice.
  - The two `pl` functions have different signatures.
  - There are two `Channel` classes and two `Character` classes.
  - `bot/cooldown.py` and `bot/commands/cooldown.py` share a name.
- **Logging:** 12 direct `logging.x()` calls should use the module logger; those bypass your per-file log routing.
- **Scripts:** move the `script_*.py` files into `scripts/`.

## What to leave alone
- **`twitchAPI/` and `utils/routines.py`:** both are third-party code.
- **`ravenfallupdater.py` and `ravenfallshutdown.py`:** they're new and have the most test coverage.
- **`bot/commands/` framework:** it works, and only needs the small deletions above.
- **`ravenpy`:** it's shared with `ravenhelper-bot`, so don't change it as part of this. Add public getters there separately to replace the 15 private `_items_*` uses.
- **Chat throttling:** no phase adds any, as decided on 2026-11-06. Since 2026-11-14, `bot/ravenbotfallback.py` resends what RavenBot drops instead.
- **`bot/messageprocessor.py` key handling:** keep both key styles until the middleman switch is finished.

## Verifying the work
Live towns run on `pc3-server`, so tests here can't cover real behaviour. Each phase should go out on its own, with a manual check of one town: restart, scroll use and a redeem. The suite takes about 4 minutes.