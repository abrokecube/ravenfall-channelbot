from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import ClassVar, cast, override

import aiohttp
from pydantic import field_validator

from bot.core.components import BaseService
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService


class PastebinProvider(StrEnum):
    BORKEDBIN = "borkedbin"
    PASTES = "pastes"


class PastebinServiceConfig(ConfigModel):
    """Pastebin service config."""

    config_table_name: ClassVar[str | None] = "services.pastebin"
    provider: PastebinProvider = PastebinProvider.BORKEDBIN
    provider_url: str | None = None
    provider_api_key: str | None = None

    @field_validator("provider_url", mode="after")
    @classmethod
    def strip_trailing_slash(cls, v: str | None) -> str | None:
        """Remove trailing slash from provider_url."""
        if v:
            return v.rstrip("/")
        return v


@dataclass
class UploadResult:
    url: str | None


class APIKeyRequiredError(Exception):
    """An API key is required for this service."""


class BaseProvider:
    def __init__(self, config: PastebinServiceConfig) -> None:
        self.base_url: str | None = config.provider_url
        self.api_key: str | None = config.provider_api_key

    async def upload_text(self, text: str) -> UploadResult:  # pyright: ignore[reportUnusedParameter]
        """Upload text to the service."""
        raise NotImplementedError


class PastesProvider(BaseProvider):
    @override
    async def upload_text(self, text: str) -> UploadResult:
        base_url = self.base_url or "https://pastes.dev"
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                f"{base_url}/post",
                headers={"Content-Type": "text/plain"},
                data=text,
            )
            if r.status == HTTPStatus.CREATED:
                return UploadResult(f"{base_url}/{(await r.json())['key']}")
            _ = await r.text()
            return UploadResult(None)


class BorkedBinProvider(BaseProvider):
    @override
    async def upload_text(self, text: str) -> UploadResult:
        if not self.api_key:
            raise APIKeyRequiredError
        base_url = self.base_url or "https://bin.borkedcube.moe"
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                f"{base_url}/api/add/text",
                headers={"X-Api-Key": self.api_key},
                json={"content": text},
            )
            if r.status == HTTPStatus.OK:
                result: dict[str, str] = cast("dict[str, str]", await r.json())
                return UploadResult(result["url"])
            _ = await r.text()
            return UploadResult(None)


PROVIDERS: dict[PastebinProvider, type[BaseProvider]] = {
    PastebinProvider.BORKEDBIN: BorkedBinProvider,
    PastebinProvider.PASTES: PastesProvider,
}


class PastebinService(BaseService, ConfigSubscriberMixin):
    """Service providing Pastebin upload."""

    def __init__(self) -> None:
        super().__init__()
        self._config: PastebinServiceConfig = PastebinServiceConfig()
        self.provider: BaseProvider = BaseProvider(self._config)

    @override
    async def setup(self) -> None:
        config_service = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_service)
        self._config = self.subscribe_config(PastebinServiceConfig)
        self.provider = PROVIDERS[self._config.provider](self._config)

    @override
    async def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if not isinstance(config, PastebinServiceConfig):
            return
        self._config = config
        self.provider = PROVIDERS[self._config.provider](self._config)

    async def upload_text(self, text: str) -> UploadResult:
        """Upload text to the configured provider."""
        return await self.provider.upload_text(text)
