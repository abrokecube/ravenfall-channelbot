# Design: `--force` flag for `rfrestart`

Date: 2026-08-26

## Summary

Add a `--force` flag to the `rfrestart` command. When set, the restart task's
countdown is never paused by in-game events (dungeons, raids, server downtime,
updater issues). The restart proceeds on schedule no matter what is happening
in the game.

## Background

The `rfrestart` command creates an `RFRestartTask` which:

- Counts down to the restart.
- Pauses the countdown via `_event_watcher` when the channel is in a
  dungeon, raid, dungeon prep, when the server is offline, when the update
  check fails, or when status checking errors.
- Waits for pending game events to be processed (`event_watch_lock`) before
  executing the restart.

## Behavior

- `--force` (alias `-f`) on `rfrestart` disables all pause events.
- The countdown still runs, warnings/announcements are still sent, and the
  task still waits for pending events to be processed before restarting.
- The pause-related checks and the "Restart postponed due to ..." messages
  are skipped entirely when `force=True`.
- The reply message to the command confirms the restart is forced.

## Changes

### `bot/cogs/game.py` — `rfrestart` command

- Add `force: bool = False` keyword parameter, decorated with
  `@parameter("force", aliases=["f"])`.
- Pass `force=force` to `channel.queue_restart(...)`.
- Reply: `"Restart queued. Restarting in {seconds}s."` plus
  `" (forced)"` when `force` is set.

### `bot/ravenfallchannel.py` — `queue_restart`

- Add `force: bool = False` keyword argument.
- Pass it through to the `RFRestartTask` constructor.

### `bot/ravenfallrestarttask.py` — `RFRestartTask`

- Add `force: bool = False` constructor argument, stored as `self.force`.
- In `_event_watcher`: when `self.force` is `True`, skip the event checks
  and the pause/unpause branches (the loop still sleeps and returns when
  `self.done`).

## Not changed

- `queue_restart`'s `monitoring_paused` guard (USER reasons already bypass it).
- The `event_watch_lock` wait in `_waiting` — pending events are still
  processed before the forced restart executes.
- Warning announcements and countdown messages.
- Other restart commands (`rfrestartcancel`, `rfrestartpostpone`, etc.).

## Testing

- Verify the `--force` flag parses on the command (bool converter).
- Verify a forced restart task does not pause during dungeon/raid events.
- Verify a non-forced restart still pauses as before.