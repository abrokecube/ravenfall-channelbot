# rfrestart --force Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--force` flag to the `rfrestart` command so a restart proceeds on schedule even during dungeons, raids, server downtime, or update-check failures.

**Architecture:** Thread a `force` boolean from the command through `RFChannel.queue_restart` into `RFRestartTask`. The task's `_event_watcher` skips all pause checks when `force=True`; the pending-events wait in `_waiting` is untouched, as are countdown warnings.

**Tech Stack:** Python 3.13, discord.py-style custom command framework, `uv` for environment management.

**Design doc:** `docs/superpowers/specs/2026-08-26-rfrestart-force-flag-design.md`

**Testing note:** Behavioral testing is not possible in this environment. Verification is limited to syntax compilation, import checks, and synchronous attribute smoke checks. Run the bot and issue `rfrestart --force` manually when the environment allows.

---

### Task 1: Add `force` to `RFRestartTask`

**Files:**
- Modify: `bot/ravenfallrestarttask.py:44-74` (constructor), `bot/ravenfallrestarttask.py:157-186` (`_event_watcher` loop)
- Test: inline `uv run python -c` smoke checks (no test file)

- [ ] **Step 1: Add the `force` constructor parameter**

In `bot/ravenfallrestarttask.py`, change the `__init__` signature (lines 45-53) from:

```python
    def __init__(
        self,
        channel: RFChannel,
        manager: RFChannelManager,
        time_to_restart: int | None = 0,
        mute_countdown: bool = False,
        label: str = "",
        reason: RestartReason | None = None
    ):
```

to:

```python
    def __init__(
        self,
        channel: RFChannel,
        manager: RFChannelManager,
        time_to_restart: int | None = 0,
        mute_countdown: bool = False,
        label: str = "",
        reason: RestartReason | None = None,
        force: bool = False
    ):
```

- [ ] **Step 2: Store `self.force`**

In the same `__init__`, after the existing line `self.reason: RestartReason | None = reason` (line 70), add:

```python
        self.force: bool = force
```

- [ ] **Step 3: Skip pause checks in `_event_watcher` when forced**

In `bot/ravenfallrestarttask.py`, in `_event_watcher` (line 157-186), the loop body currently starts:

```python
        while True:
            old_event_type = event_type
            event_type = ""
            async with self.event_watch_lock:
                await asyncio.sleep(2)
                if self.done:
                    return
            
                time_left = self.get_time_left()
                try:
```

Change it so the event checks are skipped entirely when `force` is set:

```python
        while True:
            old_event_type = event_type
            event_type = ""
            async with self.event_watch_lock:
                await asyncio.sleep(2)
                if self.done:
                    return

                if self.force:
                    continue

                time_left = self.get_time_left()
                try:
```

(The `continue` exits the `async with` block, releasing the lock, then restarts the loop. When forced, no pause/unpause logic runs, so no "Restart postponed..." messages are sent and `future_pause_reason` stays empty.)

- [ ] **Step 4: Verify syntax and imports**

Run: `uv run python -m py_compile bot/ravenfallrestarttask.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Smoke-check the constructor stores `force`**

Run:

```bash
uv run python -c "
from bot.ravenfallrestarttask import RFRestartTask, RestartReason
from bot.models import RFChannelEvent, RFChannelSubEvent

class FakeChannel:
    channel_name = 'test'
    sub_event = RFChannelSubEvent.NONE
    event = RFChannelEvent.NONE
    dungeon = {'players': 0}
    raid = {'players': 0}

class FakeManager:
    ravennest_is_online = True
    async def check_update_endpoint(self):
        return True

t1 = RFRestartTask(FakeChannel(), FakeManager(), time_to_restart=6, label='t', reason=RestartReason.USER, force=True)
assert t1.force is True, 'force=True should be stored'
t2 = RFRestartTask(FakeChannel(), FakeManager(), time_to_restart=6)
assert t2.force is False, 'force should default to False'
print('force attribute OK')
"
```

Expected: prints `force attribute OK`.

- [ ] **Step 6: Commit**

```bash
git add bot/ravenfallrestarttask.py
git commit -m "feat: add force flag to RFRestartTask to skip pause events"
```

---

### Task 2: Add `force` kwarg to `RFChannel.queue_restart`

**Files:**
- Modify: `bot/ravenfallchannel.py:1253-1270` (`queue_restart`)
- Test: inline `uv run python -c` import check

- [ ] **Step 1: Add the `force` parameter**

In `bot/ravenfallchannel.py`, change the `queue_restart` signature (line 1253) from:

```python
    def queue_restart(self, time_to_restart: int | None = None, mute_countdown: bool = False, label: str = "", reason: RestartReason | None = None, overwrite_same_reason: bool = False):
```

to:

```python
    def queue_restart(self, time_to_restart: int | None = None, mute_countdown: bool = False, label: str = "", reason: RestartReason | None = None, overwrite_same_reason: bool = False, force: bool = False):
```

- [ ] **Step 2: Pass `force` to the task constructor**

In the same method, change the `RFRestartTask` construction (line 1267) from:

```python
        self.restart_task = RFRestartTask(self, self.manager, time_to_restart, mute_countdown, label, reason)
```

to:

```python
        self.restart_task = RFRestartTask(self, self.manager, time_to_restart, mute_countdown, label, reason, force)
```

- [ ] **Step 3: Verify syntax and imports**

Run: `uv run python -m py_compile bot/ravenfallchannel.py`
Expected: no output, exit code 0.

Run (imports need the env var that `.env` has commented out):

```bash
uv run python -c "import os; os.environ['MULTICHAT_COMMAND_SERVER_PORT']='7200'; from dotenv import load_dotenv; load_dotenv(); import bot.ravenfallchannel; print('ravenfallchannel import OK')"
```

Expected: prints `ravenfallchannel import OK`.

- [ ] **Step 4: Commit**

```bash
git add bot/ravenfallchannel.py
git commit -m "feat: pass force flag through queue_restart"
```

---

### Task 3: Add `--force` flag to the `rfrestart` command

**Files:**
- Modify: `bot/cogs/game.py:193-209` (`rfrestart` command)
- Test: inline `uv run python -c` import check

- [ ] **Step 1: Add the `force` parameter decorator and argument**

In `bot/cogs/game.py`, the `rfrestart` command currently reads:

```python
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("reason", aliases=["r"])
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def rfrestart(self, ctx: CommandEvent, seconds: int = 30, *, reason: str = "User restart", channel: RFChannel = 'this'):
        """Creates a new restart task.
        
        Args:
            channel: Target channel.
        """
        channel.queue_restart(seconds, label=reason, reason=RestartReason.USER, overwrite_same_reason=True)
        await ctx.message.reply(f"Restart queued. Restarting in {seconds}s.")
```

Change it to:

```python
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("reason", aliases=["r"])
    @parameter("force", aliases=["f"])
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def rfrestart(self, ctx: CommandEvent, seconds: int = 30, *, reason: str = "User restart", channel: RFChannel = 'this', force: bool = False):
        """Creates a new restart task.
        
        Args:
            channel: Target channel.
            force: Force the restart even during dungeons, raids, or server issues.
        """
        channel.queue_restart(seconds, label=reason, reason=RestartReason.USER, overwrite_same_reason=True, force=force)
        msg = f"Restart queued. Restarting in {seconds}s."
        if force:
            msg += " (forced)"
        await ctx.message.reply(msg)
```

- [ ] **Step 2: Verify syntax and imports**

Run: `uv run python -m py_compile bot/cogs/game.py`
Expected: no output, exit code 0.

Run:

```bash
uv run python -c "import os; os.environ['MULTICHAT_COMMAND_SERVER_PORT']='7200'; from dotenv import load_dotenv; load_dotenv(); import bot.cogs.game; print('game cog import OK')"
```

Expected: prints `game cog import OK`.

- [ ] **Step 3: Commit**

```bash
git add bot/cogs/game.py
git commit -m "feat: add --force flag to rfrestart command"
```

---

### Task 4: Final verification

- [ ] **Step 1: Recompile all touched files**

Run: `uv run python -m py_compile bot/ravenfallrestarttask.py bot/ravenfallchannel.py bot/cogs/game.py`
Expected: no output, exit code 0.

- [ ] **Step 2: Confirm git state**

Run: `git status`
Expected: working tree clean (all changes committed).

- [ ] **Step 3: Report for manual testing**

Summarize what needs manual verification when the environment allows:
1. `rfrestart --force` during a raid/dungeon: countdown is not paused, restart executes on schedule.
2. `rfrestart` (no flag) during a raid/dungeon: still pauses as before.
3. `rfrestart -f` works (alias).
4. Reply message shows "(forced)" when flag is set.