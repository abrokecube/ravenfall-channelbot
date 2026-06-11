# Ravenfall ChannelBot

## Run

```
uv run main.py
```

## Lint & Typecheck

```
ruff check .
basedpyright
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
- **`bot/clients/`** — `ravenfall_middleman` (WebSocket message relay), `ravenfall_query` (REST API), `prometheus`, `rf_webops_client`, `process_watchdog_client`
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

# Fire-and-forget background task
fire_and_forget(some_coro())
```

Cogs auto-discover decorated methods as listeners. `EventReceiverMixin` does the same for non-Cog classes.

## Command System

Chat commands use the `@command()` decorator (separate from `@on_match`):

```python
from bot.integrations.commands import command, CommandEvent, parameter, ParameterType

@command(name="cmd", short_help_text="...", aliases=["c"])
@parameter("arg", parameter_type=ParameterType.FLAG)
async def my_command(self, ctx: CommandEvent, arg: str | None = None): ...
```

Commands are automatically wired via `CommandDispatcher` added in `main.py`.

## Configuration

Two config sources:

1. **`.env`** — secrets/credentials (Twitch app keys, API passwords). Loaded via `python-dotenv` at the top of `main.py`.
2. **`config.toml`** — structured config loaded by `ConfigService`. Tables validated against Pydantic models that inherit `ConfigModel` and set `config_table_name: ClassVar[str | None]`.

`channels.json` is legacy; primary channel/instance config is in `config.toml` under `[[services.ravenfall_channels.channels]]` and `[[integrations.ravenfall.instances]]`.

## Services & Channel Routing

- `RavenfallChannelService.send_global_message(text, category, instance_name)` — sends to all linked channels for an instance, respecting per-channel `categories`/`exclude_categories`
- `RavenfallChannelService.send_channel_message(text, instance_name)` — sends to primary channel only
- `GlobalMessengerService.send(text, platform, channel_id)` — low-level send to any registered platform
- Channels with `uses_ravenbot: true` auto-get `"ravenfall.global"` excluded to prevent duplicate forwards

## Conventions

- `from __future__ import annotations` at top of every file
- Google-style docstrings
- `@override` from `typing`
- `__` prefix for unused variables and return values (`__ = await asyncio.gather(...)`)
- Line length 90
- `*.py` files with `_old` suffix are legacy — do not use as structural reference
- Config models inherit `ConfigModel`, set `config_table_name: ClassVar[str | None]`
- Package cogs (`ravenfall_scroll_queue/`, `ravenfall_watcher/`) follow `__init__.py` → exports `CogClass`, `cog.py` → contains the class
- DB models inherit `Base` from `bot.db`, use SQLAlchemy async sessions via `get_async_session()`

## Key Libraries

- `msgspec` — struct definitions (Ravenfall API responses, middleman messages)
- `twitchapi` — Twitch IRC/EventSub, custom fork at `https://github.com/abrokecube/pyTwitchAPI`. Pinned via `[tool.uv.sources]` in `pyproject.toml`
- `sqlalchemy` + `aiosqlite` — database
- `pydantic` — config models
- `aiohttp` — HTTP client/server
- `fastapi` + `uvicorn` — internal web server
- `python-dotenv` — `.env` loading
- `ruamel.yaml` — translation string YAML files
- `psutil` — process monitoring
