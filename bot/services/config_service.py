"""TOML-backed configuration service with subscriber notifications."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, TypeAdapter

from bot.core.components import BaseService

if TYPE_CHECKING:
    from bot.mixins.config_subscriber import ConfigSubscriberMixin

LOGGER = logging.getLogger(__name__)


class ConfigService(BaseService):
    """Configuration service that loads and validates TOML files.

    Reads a TOML file, validates individual tables against Pydantic models,
    and notifies subscribers when the configuration is reloaded and their
    watched fields have changed.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        env_prefix: str = "BOT",
        cli_args: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._env_prefix: str = env_prefix
        self._cli_overrides: dict[str, str] = self._parse_cli_args(cli_args)
        self._path: Path = self._resolve_config_path(path)
        self._data: dict[str, object] = {}
        self._subscribers: dict[
            ConfigSubscriberMixin,
            dict[str, _SubscriptionEntry],
        ] = {}
        self._load_toml()

    def _load_toml(self) -> None:
        """Read and parse the TOML file from disk."""
        with self._path.open("rb") as f:
            self._data = tomllib.load(f)

    def _resolve_table(self, table: str) -> dict[str, object]:
        """Walk dotted keys to find a nested table.

        Args:
            table: Dot-separated table path (e.g. ``"server.database"``).

        Returns:
            The dict for the requested table.

        Raises:
            KeyError: If any segment of the path is missing.
        """
        current: dict[str, object] = self._data
        for segment in table.split("."):
            value = current.get(segment)
            if not isinstance(value, dict):
                msg = f"Table '{table}' not found in '{self._path.name}'"
                raise KeyError(msg)
            current = cast("dict[str, object]", value)
        return current

    def get_table[T](
        self,
        table: str,
        model: type[T],
    ) -> T:
        """Validate a TOML table against a type.

        Args:
            table: Dot-separated table name (e.g. ``"server"``).
            model: The type to validate against.  Can be a
                ``BaseModel`` subclass, a ``list[SomeModel]``, or any
                type supported by :class:`pydantic.TypeAdapter`.

        Returns:
            A validated instance of *model*.

        Raises:
            KeyError: If the table does not exist.
            pydantic.ValidationError: If validation fails.
        """
        raw = dict(self._resolve_table(table))
        self._apply_env_overrides(table, raw)
        self._apply_cli_overrides(table, raw)
        adapter: TypeAdapter[T] = TypeAdapter(model)
        return adapter.validate_python(raw)

    def _apply_env_overrides(
        self,
        table: str,
        raw: dict[str, object],
    ) -> None:
        """Overlay environment variable values onto a raw table dict.

        Scans ``os.environ`` for keys matching the pattern
        ``{PREFIX}_{TABLE}_{FIELD}`` (uppercased, single-underscore
        separated) and overwrites the corresponding entry in *raw*.

        Args:
            table: The dot-separated table path.
            raw: The mutable dict to overlay values into.
        """
        prefix_parts: list[str] = []
        if self._env_prefix:
            prefix_parts.append(self._env_prefix.upper())
        prefix_parts.extend(segment.upper() for segment in table.split("."))
        env_prefix = "_".join(prefix_parts) + "_"

        for env_key, env_val in os.environ.items():
            if env_key.startswith(env_prefix):
                field = env_key[len(env_prefix) :].lower()
                raw[field] = env_val

    def _parse_cli_args(self, cli_args: list[str] | None) -> dict[str, str]:
        """Parse command-line arguments for config overrides.

        Supports:
        - ``--config-path <path>``: Set the config file path
        - ``--table.field=value``: Override specific config values

        Returns a dict mapping config keys to values. The config path is
        stored under the special key ``__config_path__``.

        Args:
            cli_args: List of command-line arguments. If None, uses
                ``sys.argv[1:]``.

        Returns:
            Dict of config overrides keyed by ``table.field`` or
            ``__config_path__`` for the config file path.
        """
        if cli_args is None:
            cli_args = sys.argv[1:]

        parser = argparse.ArgumentParser(add_help=False)
        __ = parser.add_argument("--config-path", default=None, type=str)

        try:
            args, _ = parser.parse_known_args(cli_args)
            args: argparse.Namespace
        except (argparse.ArgumentError, ValueError):
            LOGGER.debug("Failed to parse CLI args, using empty overrides")
            return {}

        overrides: dict[str, str] = {}
        if args.config_path:  # pyright: ignore[reportAny]
            overrides["__config_path__"] = args.config_path  # pyright: ignore[reportAny]

        # Handle direct --table.field=value format
        for arg in cli_args:
            if arg.startswith("--") and "=" in arg and not arg.startswith("--config-"):
                key_value = arg[2:]  # Remove leading --
                if "=" in key_value:
                    key, value = key_value.split("=", 1)
                    overrides[key] = value

        return overrides

    def _resolve_config_path(self, path: Path | str | None) -> Path:
        """Resolve the config file path from multiple sources.

        Checks sources in order of precedence:
        1. Explicit ``path`` argument
        2. CLI ``--config-path`` argument
        3. Environment variable ``{PREFIX}_CONFIG_PATH`` or ``CONFIG_PATH``
        4. Default ``config.toml``

        Args:
            path: Explicit path passed to constructor.

        Returns:
            The resolved config file path.

        Raises:
            FileNotFoundError: If no valid config path can be determined.
        """
        # 1. Explicit path argument
        if path is not None:
            return Path(path)

        # 2. CLI argument
        cli_path = self._cli_overrides.get("__config_path__")
        if cli_path:
            return Path(cli_path)

        # 3. Environment variable
        env_var = f"{self._env_prefix}_CONFIG_PATH" if self._env_prefix else "CONFIG_PATH"
        env_path = os.environ.get(env_var.upper())
        if env_path:
            return Path(env_path)

        # 4. Default
        return Path("config.toml")

    def _apply_cli_overrides(
        self,
        table: str,
        raw: dict[str, object],
    ) -> None:
        """Overlay command-line argument values onto a raw table dict.

        Scans CLI overrides for keys matching the pattern
        ``table.field`` and overwrites the corresponding entry in *raw*.
        CLI overrides take precedence over environment variables.

        Args:
            table: The dot-separated table path.
            raw: The mutable dict to overlay values into.
        """
        for key, value in self._cli_overrides.items():
            if key.startswith(table + "."):
                field = key[len(table + ".") :]
                if field in raw:
                    raw[field] = value

    def reload(self) -> None:
        """Re-read the TOML file and notify subscribers of changes.

        For each subscription, the new model is validated and compared
        field-by-field to the cached snapshot.  If any top-level fields
        differ, ``on_config_changed`` is called on the subscriber with
        the new model and the set of changed field names.
        """
        LOGGER.info("Reloading config from '%s'", self._path)
        self._load_toml()

        for subscriber, entries in self._subscribers.items():
            for table, entry in entries.items():
                try:
                    new_value = self.get_table(table, entry.model_type)
                except Exception:
                    LOGGER.exception(
                        "Failed to validate table '%s' for subscriber %s during reload",
                        table,
                        type(subscriber).__name__,
                    )
                    continue

                changed = _diff_snapshots(entry.snapshot, new_value)
                if changed:
                    entry.snapshot = new_value
                    LOGGER.debug(
                        "Notifying %s of changes to table '%s': %s",
                        type(subscriber).__name__,
                        table,
                        changed,
                    )
                    try:
                        subscriber.on_config_changed(table, new_value, changed)
                    except Exception:
                        LOGGER.exception(
                            "Error in on_config_changed for %s",
                            type(subscriber).__name__,
                        )

    # ------------------------------------------------------------------
    # Subscription management (called by ConfigSubscriberMixin)
    # ------------------------------------------------------------------

    def _subscribe[T](
        self,
        subscriber: ConfigSubscriberMixin,
        table: str,
        model_type: type[T],
    ) -> None:
        """Register a subscriber for a specific table.

        Args:
            subscriber: The mixin instance subscribing.
            table: Dot-separated TOML table name.
            model_type: The type to validate against.
        """
        snapshot = self.get_table(table, model_type)
        entry = _SubscriptionEntry(model_type=model_type, snapshot=snapshot)
        self._subscribers.setdefault(subscriber, {})[table] = entry
        LOGGER.debug(
            "%s subscribed to table '%s'",
            type(subscriber).__name__,
            table,
        )

    def _unsubscribe(
        self,
        subscriber: ConfigSubscriberMixin,
        table: str,
    ) -> None:
        """Remove a subscriber's registration for a specific table.

        Args:
            subscriber: The mixin instance unsubscribing.
            table: Dot-separated TOML table name to stop watching.

        Raises:
            KeyError: If the subscriber is not subscribed to *table*.
        """
        entries = self._subscribers.get(subscriber)
        if entries is None or table not in entries:
            msg = f"{type(subscriber).__name__} is not subscribed to table '{table}'"
            raise KeyError(msg)
        del entries[table]
        if not entries:
            del self._subscribers[subscriber]
        LOGGER.debug(
            "%s unsubscribed from table '%s'",
            type(subscriber).__name__,
            table,
        )


class _SubscriptionEntry:
    """Tracks the model type and last-known snapshot for a subscription."""

    __slots__: tuple[str, ...] = ("model_type", "snapshot")

    def __init__(
        self,
        model_type: type[object],
        snapshot: object,
    ) -> None:
        self.model_type: type[object] = model_type
        self.snapshot: object = snapshot


def _diff_snapshots(
    old: object,
    new: object,
) -> set[str]:
    """Compare two config snapshots and return names of changed fields.

    For ``BaseModel`` instances, compares top-level fields individually.
    For any other type, performs a simple equality check and returns
    ``{"__value__"}`` if the values differ.

    Args:
        old: The previous snapshot.
        new: The newly validated value.

    Returns:
        A set of changed field names, or ``{"__value__"}`` for
        non-model types that differ.
    """
    if isinstance(new, BaseModel) and isinstance(old, BaseModel):
        changed: set[str] = set()
        for field_name in type(new).model_fields:
            old_val = getattr(old, field_name, _SENTINEL)
            new_val = getattr(new, field_name, _SENTINEL)
            if old_val != new_val:
                changed.add(field_name)
        return changed

    if old != new:
        return {"__value__"}
    return set()


_SENTINEL = object()
