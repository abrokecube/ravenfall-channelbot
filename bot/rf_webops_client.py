import aiohttp
import asyncio
from typing import List, Dict, Any

class WebOpsClient:
    def __init__(self, base_url: str = "http://127.0.0.1:7102"):
        self.base_url = base_url.rstrip("/")

    async def redeem_items(self, item_id: str, quantity: int, characters: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Redeem items for a list of characters.
        characters: List of dicts with 'username' and 'id'.
        """
        url = f"{self.base_url}/redeem"
        payload = {
            "item_id": item_id,
            "quantity": quantity,
            "characters": characters
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60*15)) as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Redemption failed: {response.status} - {text}")
                return await response.json()

    async def get_total_loyalty_points(self, usernames: List[str]) -> Dict[str, Any]:
        """
        Get total loyalty points for a list of usernames.
        """
        url = f"{self.base_url}/loyalty/points"
        payload = {
            "usernames": usernames
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60*15)) as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Failed to get points: {response.status} - {text}")
                return await response.json()

