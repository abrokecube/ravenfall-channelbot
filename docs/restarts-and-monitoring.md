# Restarts and monitoring

Each town runs in a Sandboxie box. `utils/runshell.py` wraps process control
(`stop_process`, `start_process`, `restart_process`). A box can hold several towns,
and RavenBot may be shared by them.

## RavenBot watchdog

The watchdog checks that RavenBot answers monitored commands. It lives in
`bot/ravenfallchannel.py`. Only commands listed in `COMMAND_TIMEOUTS` are watched,
each with its own timeout.

`monitor_ravenfall_command` runs for chat from viewers (`event_twitch_message`) and
for the bot's own messages (`send_chat_message`). Then `monitor_ravenbot_response`
starts `_monitor_ravenbot_response_task`, which does nothing when any of these holds:

- `monitoring_paused` is set
- a check is already running (`is_monitoring`)
- the updater is holding restarts (`holding_restarts`)
- the town is in `town_shutdown.held`
- a restart is in progress (`_restart_in_progress`)

`_await_ravenbot_response` decides what counts as a reply. It keeps waiting while a
dungeon is preparing. With the middleman connected, any message from RavenBot counts
(`ravenbot_waiter`). Without it, only a chat message from `RAVENBOT_USER_ID` counts
(`twitch_message_waiter`). A silence that overlaps a restart is not a failure.

| Attempt | Action |
| --- | --- |
| 1, 2 | `restart_ravenbot`, then retry the command |
| 3 | `queue_restart(10, reason=UNRESPONSIVE)` for the game |
| After that | Gives up and pings the owner |

The count resets once `RETRY_WINDOW` passes. Commands the bot sent itself set
`resend_text`, so the bot resends them after a restart. Viewers have to resend
their own.

A missed reply can also mean RavenBot hit its chat limit of 20 messages per 30
seconds per channel. The game has nothing to do with that case.

`pausemon`, `resumemon` and `togglebotmonitor` all use `monitoring_paused`, but
differently. `pausemon` and `resumemon` stop and start the whole `RFChannel`, while
`togglebotmonitor` only flips the flag and leaves routines running.

## Restart tasks

`RFChannel.queue_restart` creates an `RFRestartTask` from
`bot/ravenfallrestarttask.py`. It refuses and returns nothing in these cases:

- `holding_restarts` is set, except for `GAME_UPDATE`
- the town is in `town_shutdown.held`, except for `SHUTDOWN`
- `monitoring_paused` is set, except for `USER`, `GAME_UPDATE` and `SHUTDOWN`
- a task with the same reason exists and `overwrite_same_reason` is false

`RestartReason` values and what queues them:

| Reason | Source |
| --- | --- |
| `AUTO` | `auto_restart_routine`, on `restart_period` (minimum 20 minutes) |
| `USER` | `rfrestart`, the "Restart Ravenfall" redeem |
| `UNRESPONSIVE` | Watchdog, or `auto_restart_routine` when there's no game session |
| `MULTIPLIER_DESYNC` | `mult_check_routine` |
| `ITEM_DESYNC` | `ItemAlertMonitor` |
| `MEMORY_USE` | `RAMUsageAlertMonitor` |
| `GAME_UPDATE` | `RFGameUpdater` |
| `SHUTDOWN` | `RFTownShutdown` |

### Task lifecycle

`_waiting` counts down, with warnings and the pre-restart step at `WARNING_MSG_TIMES`.
The last entry runs `_ravenfall_pre_restart` at 20 seconds left, which saves training
data and sends `?randleave`.

`_event_watcher` runs alongside it and pauses the countdown during a dungeon, dungeon
preparation or raid, and when the game server or updater is down. `peers` holds the
other towns' tasks during an update, so one town's event holds them all. `force`
skips all of these checks. Resuming makes sure at least 60 seconds remain.

When the countdown ends, `before_stop` runs, then `_execute` calls `_stop_ravenfall`
for a shutdown or `_restart_ravenfall` otherwise. `postpone`, `pause`, `unpause` and
`cancel` are the controls, and `postpone_restart` and `cancel_restart` are their
`RFChannel` wrappers.

### The restart itself

`_restart_ravenfall` holds `channel_restart_lock` and `global_restart_lock` while
stopping and starting the game, retrying until `_launch_ravenfall` succeeds. Its
`before_start` hook runs after the stop, while the game is down, and holds the channel
lock but not the global one. The updater uses it to install files.

`_ravenfall_post_restart` waits for players, sends `?undorandleave`, and calls
`restore_auto_raids` if the middleman is connected and `auto_restore_raids` is on.
`channel_post_restart_lock` covers this step.

`restore_sailors` exists but isn't called from post restart at the moment.

## Monitors

Both are in `bot/ravenfallmanager.py` and extend `BatchAlertMonitor` from
`utils/alert_monitor.py`.

- `ItemAlertMonitor` restarts a town whose item count hasn't grown, for
  `ITEM_DESYNC`.
- `RAMUsageAlertMonitor` reads Prometheus and Sandboxie process lists against
  `MAX_RAVENFALL_MIB` and `MAX_RAVENFALL_TOTAL_MIB`, for `MEMORY_USE`.

Other routines in `RFChannel`: `dungeon_killswitch_routine` stops dungeons longer
than `MAX_DUNGEON_LENGTH`, and `backup_state_data_routine` backs up `state-data.json`
from the sandboxed folder.

## Updater

`RFGameUpdater` in `bot/ravenfallupdater.py` (`rfupdate`, with `--check`, `--force`
and `--cancel`) updates all towns from upstream `zerratar/Ravenfall-Legacy`. The
module docstring has the details. In short:

1. `fetch_latest_release` compares `GameRelease.is_newer_than` with
   `installed_versions`.
2. Towns pre-download `update.7z` to `update/` inside each box, with a `ready.json`
   marker, so `copy_predownloaded_update` tries that before `download_asset`.
3. `extract_archive` needs 7-Zip (`find_seven_zip`, `SEVEN_ZIP_PATH`) because the
   archive uses BCJ2, which py7zr can't read.
4. `holding_restarts` and `holding_scrolls` are set so nothing else interferes.
   `finish_running_restarts` lets running tasks end, and
   `_wait_for_events_to_finish` waits for events.
5. `_queue_restarts` queues one `GAME_UPDATE` restart per town. All towns must go
   down together. `TownGate` holds them in `town_ready` (the `before_stop` hook)
   until all can stop, and in `town_stopped` (the `before_start` hook) until all
   are down and the install has run. The last town to stop runs the install.
6. `install_files` copies files after `wait_for_unlocked_files`. `install_ravenbot`
   updates RavenBot once per box, through `_ravenbot_channels`.

RavenWeave must not be installed, because the game moves anything in `updater/` into
its own folder on startup. `iter_update_files` skips `updater/`, and `install_files`
deletes `RavenWeave.exe` and `updater/` from the game folder and the box folders.

The fakes in `tests/test_ravenfall_updater.py` run real restart tasks against a
faked game and Sandboxie, so they show the expected order.

## Town shutdown

`RFTownShutdown` in `bot/ravenfallshutdown.py` queues a `SHUTDOWN` task, which stops
instead of restarting. `rfshutdown`, `rfshutdown cancel` and `rfstart` act on the
current town (or `--channel`), and `--all`, `rfstopall` and `rfstartall` act on every
town. A town in
`held` has its watchdog, other restarts and scroll queue blocked, and `active` means
any town is held, which blocks `rfupdate`. `MIN_COUNTDOWN` keeps the countdown longer
than the pre-restart step.

`rfshutdown cancel` calls off a shutdown that hasn't finished and leaves towns already
down alone. A cancelled town keeps running, unless the pre-restart step already sent
its players away, in which case it restarts. `rfstart` does the same, and also starts
towns that are down.

The hold lives only in memory, so restarting the bot brings the towns back.
