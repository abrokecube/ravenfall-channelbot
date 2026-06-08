# Ravenfall ChannelBot

## Run

```
uv run main.py
```

## Lint & Typecheck

```
ruff check .
basedpyright
pyrefly check
```

Configured in `pyproject.toml`: ruff `--select ALL` with many ignores, line-length 90.

## Architecture

Custom event-driven system wired in `main.py`:

```
EventManager → BaseDispatcher (by category matching) → BaseListener (by @on_match)
```

- **`bot/core/`** — EventManager, BaseDispatcher, BaseListener, Cog, BaseService, decorators
- **`bot/integrations/{twitch,ravenfall,commands,chat_messages,process_manager}/`** — platform event sources + events
- **`bot/cogs/`** — plugin modules (inherit `Cog`), registered via `event_manager.add_cog(Cls)` in `main.py`
- **`bot/services/`** — singletons (inherit `BaseService`), registered via `global_context.register_service(inst)`
- **`bot/mixins/`** — reusable traits: `EventReceiverMixin` (services listen to events), `ConfigSubscriberMixin` (subscribe config tables)
- **`bot/clients/`** — `ravenfall_middleman` (WebSocket message relay), `ravenfall_query` (REST API)
- **`utils/`** — `@routine(delta=timedelta(...))` for periodic tasks, `strutils` for text helpers, `format_time` for time formatting

## Event System Patterns

```python
# Listen for an event type
@on_match(MyEvent)
async def handler(self, _g_ctx, event: MyEvent, _match): ...

# Listen with filter
@on_match(MyEvent, lambda e: e.some_field == "value")
async def handler(self, _g_ctx, event: MyEvent, _match): ...

# Set execution priority (higher = earlier)
@priority(10) @on_match(MyEvent)
async def handler(self, _g_ctx, event: MyEvent, _match): ...

# Periodic task
@routine(delta=timedelta(seconds=30))
async def my_routine(self): ...
# Start in setup(): __ = self.my_routine.start()
# Stop in teardown(): self.my_routine.stop()
```

Cogs auto-discover decorated methods as listeners. `EventReceiverMixin` does the same for non-Cog classes.

## Services & Channel Routing

- `RavenfallChannelService.send_global_message(text, category, instance_name)` — sends to all linked channels for an instance, respecting per-channel `categories`/`exclude_categories`
- `RavenfallChannelService.send_channel_message(text, instance_name)` — sends to primary channel only
- `GlobalMessengerService.send(text, platform, channel_id)` — low-level send to any registered platform
- Channels with `uses_ravenbot: true` auto-get `"ravenfall.global"` excluded to prevent duplicate forwards

## Conventions

- `from __future__ import annotations` at top of every file
- Google-style docstrings
- `@override` from `typing`
- `__` prefix for unused variables
- Line length 90
- `*.py` files with `_old` suffix are legacy — do not use as structural reference
- Config models inherit `ConfigModel`, set `config_table_name: ClassVar[str | None]`
- Package cogs (`ravenfall_scroll_queue/`, `ravenfall_watcher/`) follow `__init__.py` → exports `CogClass`, `cog.py` → contains the class

## Key Libraries

- `msgspec` — struct definitions (Ravenfall API responses, middleman messages)
- `twitchapi` — Twitch IRC/EventSub, custom fork at `https://github.com/abrokecube/pyTwitchAPI`
- `sqlalchemy` + `aiosqlite` — database
- `pydantic` — config models
- `aiohttp` — HTTP client/server
- `fastapi` + `uvicorn` — internal web server
