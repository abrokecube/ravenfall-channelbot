"""TOML-backed configuration service with subscriber notifications."""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from bot.core.components import BaseService

LOGGER = logging.getLogger(__name__)


class ConfigService(BaseService):
    """Configuration service that loads and validates TOML files.

    Reads a TOML file, validates individual tables against Pydantic models,
    and notifies subscribers when the configuration is reloaded and their
    watched fields have changed.
    """

    def __init__(
        self,
        path: Path | str,
        env_prefix: str = "",
    ) -> None:
        super().__init__()
        self._path: Path = Path(path)
        self._env_prefix: str = env_prefix
        self._data: dict[str, object] = {}
        self._subscribers: dict[
            ConfigSubscriberMixin,
            dict[str, _SubscriptionEntry],
        ] = {}
        self._load()

    def _load(self) -> None:
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

    def get_table[T: BaseModel](
        self,
        table: str,
        model: type[T],
    ) -> T:
        """Validate a TOML table against a Pydantic model.

        Args:
            table: Dot-separated table name (e.g. ``"server"``).
            model: The Pydantic model class to validate against.

        Returns:
            A validated instance of *model*.

        Raises:
            KeyError: If the table does not exist.
            pydantic.ValidationError: If validation fails.
        """
        raw = dict(self._resolve_table(table))
        self._apply_env_overrides(table, raw)
        return model.model_validate(raw)

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
                if field in raw:
                    raw[field] = env_val

    def reload(self) -> None:
        """Re-read the TOML file and notify subscribers of changes.

        For each subscription, the new model is validated and compared
        field-by-field to the cached snapshot.  If any top-level fields
        differ, ``on_config_changed`` is called on the subscriber with
        the new model and the set of changed field names.
        """
        self._load()

        for subscriber, entries in self._subscribers.items():
            for table, entry in entries.items():
                try:
                    new_model = self.get_table(table, entry.model_type)
                except Exception:
                    LOGGER.exception(
                        "Failed to validate table '%s' for subscriber %s during reload",
                        table,
                        type(subscriber).__name__,
                    )
                    continue

                changed = _diff_models(entry.snapshot, new_model)
                if changed:
                    entry.snapshot = new_model
                    try:
                        subscriber.on_config_changed(new_model, changed)
                    except Exception:
                        LOGGER.exception(
                            "Error in on_config_changed for %s",
                            type(subscriber).__name__,
                        )

    # ------------------------------------------------------------------
    # Subscription management (called by ConfigSubscriberMixin)
    # ------------------------------------------------------------------

    def _subscribe(
        self,
        subscriber: ConfigSubscriberMixin,
        table: str,
        model_type: type[BaseModel],
    ) -> None:
        """Register a subscriber for a specific table.

        Args:
            subscriber: The mixin instance subscribing.
            table: Dot-separated TOML table name.
            model_type: The Pydantic model to validate against.
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
        model_type: type[BaseModel],
        snapshot: BaseModel,
    ) -> None:
        self.model_type: type[BaseModel] = model_type
        self.snapshot: BaseModel = snapshot


def _diff_models(
    old: BaseModel,
    new: BaseModel,
) -> set[str]:
    """Compare two Pydantic models and return names of changed fields.

    Args:
        old: The previous model snapshot.
        new: The newly validated model.

    Returns:
        A set of top-level field names whose values differ.
    """
    changed: set[str] = set()
    for field_name in type(new).model_fields:
        old_val = getattr(old, field_name, _SENTINEL)
        new_val = getattr(new, field_name, _SENTINEL)
        if old_val != new_val:
            changed.add(field_name)
    return changed


_SENTINEL = object()


class ConfigSubscriberMixin:
    """Mixin that adds config-change subscription to any class.

    Designed for multiple inheritance, e.g.::

        class MyService(BaseService, ConfigSubscriberMixin):
            ...
    """

    _config_service: ConfigService | None = None

    def inject_config_service(self, config_service: ConfigService) -> None:
        """Store a reference to the ``ConfigService``.

        Args:
            config_service: The config service instance to use.
        """
        self._config_service = config_service

    def _require_config_service(self) -> ConfigService:
        """Return the injected config service or raise."""
        svc: ConfigService | None = getattr(self, "_config_service", None)
        if svc is None:
            msg = (
                "ConfigService has not been injected. Call inject_config_service() first."
            )
            raise RuntimeError(msg)
        return svc

    def subscribe[T: BaseModel](
        self,
        table: str,
        model: type[T],
    ) -> None:
        """Subscribe to changes on a specific config table.

        Args:
            table: Dot-separated TOML table name.
            model: The Pydantic model to validate against.
        """
        svc = self._require_config_service()
        svc._subscribe(self, table, model)

    def unsubscribe(self, table: str) -> None:
        """Unsubscribe from a specific config table.

        Args:
            table: The table name to stop watching.
        """
        svc = self._require_config_service()
        svc._unsubscribe(self, table)

    def on_config_changed(
        self,
        _config: BaseModel,
        _changed_fields: set[str],
    ) -> None:
        """Called when subscribed config fields change after a reload.

        Override this method to react to configuration changes.

        Args:
            config: The newly validated Pydantic model.
            changed_fields: Set of top-level field names that changed.
        """
