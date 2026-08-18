# Design: Update `script_*_old.py` files to work with the current codebase

Date: 2026-08-18

## Goal

Make the 11 root-level `script_*_old.py` files functional with the current
codebase state, tidy the ones that already work, and rename them to drop the
`_old` suffix (which per `AGENTS.md` marks legacy files).

## Scope

Only the 11 standalone scripts are changed. No changes to the bot itself
(`bot/`, `utils/`, `ravenpy/`, config) and no changes to other files except:
- a new small helper module `utils/script_helpers.py`
- a new spec file under `docs/superpowers/specs/`

## Background / findings

- `bot.multichat_command` no longer exists. It was replaced by
  `bot/clients/ravenfall_multichat.py` exposing `RavenfallMultichatClient`.
  Four scripts import the old module and are broken:
  - `script_checkcharstock_old.py`
  - `script_grabmarketplace_old.py`
  - `script_sellall_old.py`
  - `script_stockall_old.py`
- The multichat client's data model is Struct-based, not dict-based:
  - `get_char_items(channel_id) -> list[CharItems]` where each has
    `.twitch_id`, `.user_name`, `.char_index`, `.items` (list of `CharItem`
    with `.id`, `.amount`, `.soulbound`, `.equipped`), and `.items_dict`.
  - `get_char_info() -> list[CharInfo]` where each has `.id`, `.name`,
    `.user_name`, `.index`, etc.
- The client needs a base URL. The single source of truth is
  `config.toml` → `[services.ravenfall_multichat] url` (currently
  `http://pc3-server:7200`).
- The `ravenpy` module (top-level `ravenpy/` package) is unchanged and its
  public API (`RavenNest`, `Item`, `Ingredient`, `get_all_items`,
  `_items_id_data`, `_items_name_data`, `ItemCategory`, `Skills`, `Effects`,
  `Items`) is still compatible with the scripts.
- `utils/format_time.py` (`format_seconds`, `TimeSize`), `utils/strutils.py`
  (`strjoin`), and `utils/websocket_client.py` (`AutoReconnectingWebSocket`)
  all still exist with compatible APIs.
- `script_test_old.py` active code is broken scaffolding
  (`EventManager()` missing `GlobalContext`, passing a class to
  `add_event_source`, mostly-commented-out body).
- No other files in the repo reference the `script_*_old.py` filenames, so
  renaming is safe.

## Design

### 1. New shared helper: `utils/script_helpers.py`

A tiny module used by the multichat-dependent scripts so the base URL is only
defined once (in `config.toml`).

```python
def get_multichat_url() -> str:
    """Read the multichat base URL from config.toml."""
    with Path("config.toml").open("rb") as f:
        config = tomllib.load(f)
    return config["services"]["ravenfall_multichat"]["url"]

def get_multichat_client() -> RavenfallMultichatClient:
    """Create a RavenfallMultichatClient configured from config.toml."""
    return RavenfallMultichatClient(get_multichat_url())
```

- Uses stdlib `tomllib` (Python 3.13), so no new dependency.
- Raises a clear `KeyError`/`OSError` if `config.toml` is missing or the table
  is absent, which is acceptable for a manually-run script.
- Follows repo conventions: `from __future__ import annotations`, Google-style
  docstrings, `@override` where applicable.

### 2. The four broken scripts

Replace the old `import bot.multichat_command as mc` block and the
`mc.get_char_items(...)` / `mc.get_char_info()` calls with a client from
`utils.script_helpers.get_multichat_client()`, adapting the data access from
dicts to Struct attributes:

| Script | Old call | New call | Data adaptation |
| --- | --- | --- | --- |
| `script_checkcharstock` | `mc.get_char_items(CHANNEL_ID)` | `client.get_char_items(CHANNEL_ID)` | `for char in char_items:` + `char.twitch_id`, `for item in char.items:` + `item.id`, `item.amount` |
| `script_grabmarketplace` | `mc.get_char_info()` | `client.get_char_info()` | `char_ids = {x.id for x in chars}` |
| `script_sellall` | `mc.get_char_items(CHANNEL_ID)` | `client.get_char_items(CHANNEL_ID)` | same as checkcharstock |
| `script_stockall` | `mc.get_char_items(CHANNEL_ID)` | `client.get_char_items(CHANNEL_ID)` | `for user in char_items:` + `user.items`, `user_item.soulbound`/`equipped`/`id`/`amount` |

All four keep the same `CHANNEL_ID`/`CHAR_ID` constants and output behavior.
`script_grabmarketplace` gets an `if __name__ == "__main__":` guard around
`asyncio.run(main())`.

### 3. Tidy the already-working scripts

Behavior unchanged; only cleanup so they pass `ruff` and are consistent with
repo conventions:

- `script_baseitemvalues` — remove unused imports; write JSON via `Path`.
- `script_getrootingredientsfromcmds` — remove unused imports (`math`,
  `tqdm`, `defaultdict`/`Optional`/`Tuple` as applicable); keep logic.
- `script_item_cooldowns` — remove unused imports; write CSV via `Path`.
- `script_itemcreditprice` — remove unused imports; use `Callable` instead of
  builtin `callable`; guard `asyncio.run`.
- `script_list_item_effects` — remove unused imports; keep effect names map.
- `script_ws_test` — add module/function docstrings; use `asyncio.Event`
  instead of `sleep(999…)`; trailing newline.
- The stray `# pyright: basic` headers are removed (repo uses `basedpyright`;
  keeping them is optional but they add no value once the files are clean).

All scripts get missing module/function docstrings, `from __future__ import
annotations` (repo convention), and `Path`/`tomllib` usage where relevant.

### 4. `script_test` — minimal event-system demo

Replace the broken scaffold with a small, runnable, network-free demo of the
current event system:

- `GlobalContext()` + `EventManager(global_ctx)`.
- A tiny custom `BaseEventSource` subclass that emits a single `BaseEvent`
  with category `EVENT_CATEGORY_GENERIC`.
- A small `Cog` with an `@on_match` listener that prints when it receives the
  event.
- `main()` adds the cog and dispatches one event, then exits.

This demonstrates the current API (`EventManager.add_cog`,
`add_event_source`, `@on_match`) and runs standalone with `uv run script_test.py`.

### 5. Rename files

`git mv` each `script_*_old.py` → `script_*.py` (drop `_old`) for all 11 files,
preserving history. No other code references the old names.

## Files touched

- `utils/script_helpers.py` (new)
- `script_baseitemvalues_old.py` → `script_baseitemvalues.py`
- `script_checkcharstock_old.py` → `script_checkcharstock.py`
- `script_getrootingredientsfromcmds_old.py` → `script_getrootingredientsfromcmds.py`
- `script_grabmarketplace_old.py` → `script_grabmarketplace.py`
- `script_item_cooldowns_old.py` → `script_item_cooldowns.py`
- `script_itemcreditprice_old.py` → `script_itemcreditprice.py`
- `script_list_item_effects_old.py` → `script_list_item_effects.py`
- `script_sellall_old.py` → `script_sellall.py`
- `script_stockall_old.py` → `script_stockall.py`
- `script_test_old.py` → `script_test.py`
- `script_ws_test_old.py` → `script_ws_test.py`

## Error handling

- `get_multichat_url()` raises on missing `config.toml` or missing table; the
  scripts are manual tools so a traceback with a clear message is acceptable.
- Scripts that need live network (`RavenNest` login, multichat server) are not
  run during verification; they are verified by import/compile/lint checks.

## Testing / verification

- `uvx ruff check script_*.py` → target 0 errors.
- `uvx basedpyright script_*.py` → clean where feasible; dict-heavy scripts may
  keep a small number of justified `# pyright:` suppressions.
- `uv run python -m py_compile script_*.py` → all compile.
- Import smoke test for non-network scripts where safe.
