"""Client for the game's HTTP query API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .session import get_session

logger = logging.getLogger(__name__)


class QueryClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def get(self, query: str, timeout: int = 5, suppress_timeout_error: bool = False) -> Any:
        """Runs a query such as `select * from session`. Returns None on every failure."""
        try:
            request = get_session().get(f"{self.base_url}/{query}", timeout=aiohttp.ClientTimeout(total=timeout))
            async with request as response:
                return await response.json()
        except asyncio.TimeoutError:
            if not suppress_timeout_error:
                logger.error(f"Timeout fetching Ravenfall query from {self.base_url}")
            return None
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Error fetching Ravenfall query from {self.base_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching Ravenfall query from {self.base_url}: {e}", exc_info=True)
            return None
