from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.services.config_service import ConfigService


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

    def subscribe_config[T](
        self,
        table: str,
        model: type[T],
    ) -> T:
        """Subscribe to changes on a specific config table.

        Args:
            table: Dot-separated TOML table name.
            model: The type to validate against.
        """
        svc = self._require_config_service()
        svc._subscribe(self, table, model)
        if not self._config_service:
            raise RuntimeError
        return self._config_service.get_table(table, model)

    def unsubscribe_config(self, table: str) -> None:
        """Unsubscribe from a specific config table.

        Args:
            table: The table name to stop watching.
        """
        svc = self._require_config_service()
        svc._unsubscribe(self, table)

    def on_config_changed(
        self,
        table: str,  # pyright: ignore[reportUnusedParameter]
        config: object,  # pyright: ignore[reportUnusedParameter]
        changed_fields: set[str],  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Called when subscribed config fields change after a reload.

        Override this method to react to configuration changes.

        Args:
            table: The config table name.
            config: The newly validated config value.
            changed_fields: Set of top-level field names that changed,
                or ``{"__value__"}`` for non-model types.
        """
