import aiohttp
from typing import Dict, Any

class ProcessWatcherClient:
    def __init__(self, base_url: str = "http://localhost:8110"):
        self.base_url = base_url.rstrip('/')

    async def get_processes(self) -> Dict[str, str]:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/processes") as response:
                response.raise_for_status()
                return await response.json()

    async def start_process(self, name: str) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/processes/{name}/start") as response:
                response.raise_for_status()
                return await response.json()

    async def stop_process(self, name: str) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/processes/{name}/stop") as response:
                response.raise_for_status()
                return await response.json()

    async def restart_process(self, name: str) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/processes/{name}/restart") as response:
                response.raise_for_status()
                return await response.json()

    async def git_pull(self, name: str) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/processes/{name}/git-pull") as response:
                response.raise_for_status()
                return await response.json()

    async def reload_config(self) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/config/reload") as response:
                response.raise_for_status()
                return await response.json()
