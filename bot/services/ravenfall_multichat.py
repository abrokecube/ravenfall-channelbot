from typing import override

from pydantic import BaseModel

from bot.clients.ravenfall_multichat import RavenfallMultichatClient
from bot.core.components import BaseService
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigService


class MultichatConfig(BaseModel):
    """Multichat config."""

    url: str


class RavenfallMultichatService(BaseService, ConfigSubscriberMixin):
    """Service that holds a single ravenfall-multichat client."""

    def __init__(self) -> None:
        super().__init__()
        self.client: RavenfallMultichatClient | None = None

    @override
    async def setup(self) -> None:
        config_service: ConfigService = await self.global_context.wait_for_service(
            ConfigService
        )
        self.inject_config_service(config_service)
        config = self.subscribe_config("services.ravenfall_multichat", MultichatConfig)
        self.client = RavenfallMultichatClient(config.url)

    def get_client(self):
        """Get the client instance."""
        if self.client:
            return self.client
        msg = "Service has not been set up."
        raise RuntimeError(msg)

    @override
    def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if not isinstance(config, MultichatConfig):
            return
        if "url" in changed_fields:
            self.client = RavenfallMultichatClient(config.url)
