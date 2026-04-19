from enum import StrEnum
from typing import Literal, NotRequired, TypedDict, cast

import aiohttp


class ProcStatus(StrEnum):
    RUNNING = "Running"
    STOPPED_MANUAL = "Stopped (Manual)"
    STOPPED = "Stopped"


class Response(TypedDict):
    status: Literal["success"]
    message: str


class ProcessResponse(TypedDict):
    name: str
    status: Literal["success"]
    message: str


class GitPullLatestCommit(TypedDict):
    hash: str
    author: str
    message: str


class GitPullResponse(TypedDict):
    name: str
    status: Literal["success", "error"]
    output: str
    error: str
    latest_commit: NotRequired[GitPullLatestCommit]


class WatchdogClientError(BaseException):
    """Error occurred while calling the client."""


def _raise_for_status(response: aiohttp.ClientResponse):
    try:
        response.raise_for_status()
    except aiohttp.ClientError as e:
        raise WatchdogClientError from e


class ProcessWatcherClient:
    def __init__(self, base_url: str = "http://localhost:8110"):
        self.base_url: str = base_url.rstrip("/")

    async def get_processes(self) -> dict[str, ProcStatus]:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{self.base_url}/processes") as response,
        ):
            _raise_for_status(response)
            return cast("dict[str, ProcStatus]", await response.json())

    async def start_process(self, name: str) -> ProcessResponse:
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{self.base_url}/processes/{name}/start") as response,
        ):
            _raise_for_status(response)
            return cast("ProcessResponse", await response.json())

    async def stop_process(self, name: str) -> ProcessResponse:
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{self.base_url}/processes/{name}/stop") as response,
        ):
            _raise_for_status(response)
            return cast("ProcessResponse", await response.json())

    async def restart_process(self, name: str) -> ProcessResponse:
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{self.base_url}/processes/{name}/restart") as response,
        ):
            _raise_for_status(response)
            return cast("ProcessResponse", await response.json())

    async def git_pull(self, name: str) -> GitPullResponse:
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{self.base_url}/processes/{name}/git-pull") as response,
        ):
            _raise_for_status(response)
            return cast("GitPullResponse", await response.json())

    async def reload_config(self) -> Response:
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{self.base_url}/config/reload") as response,
        ):
            _raise_for_status(response)
            return cast("Response", await response.json())
