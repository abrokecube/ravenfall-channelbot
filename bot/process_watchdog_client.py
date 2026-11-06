from typing import Dict, Any

from .clients.session import get_session

class ProcessWatcherClient:
    def __init__(self, base_url: str = "http://localhost:8110"):
        self.base_url = base_url.rstrip('/')

    async def _post(self, path: str) -> Dict[str, Any]:
        async with get_session().post(f"{self.base_url}/{path}") as response:
            response.raise_for_status()
            return await response.json()

    async def get_processes(self) -> Dict[str, str]:
        async with get_session().get(f"{self.base_url}/processes") as response:
            response.raise_for_status()
            return await response.json()

    async def start_process(self, name: str) -> Dict[str, Any]:
        return await self._post(f"processes/{name}/start")

    async def stop_process(self, name: str) -> Dict[str, Any]:
        return await self._post(f"processes/{name}/stop")

    async def restart_process(self, name: str) -> Dict[str, Any]:
        return await self._post(f"processes/{name}/restart")

    async def git_pull(self, name: str) -> Dict[str, Any]:
        return await self._post(f"processes/{name}/git-pull")

    async def reload_config(self) -> Dict[str, Any]:
        return await self._post("config/reload")
