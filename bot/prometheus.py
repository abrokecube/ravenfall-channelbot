import time
import aiohttp
from typing import List, TypedDict
import os

class PrometheusMetric(TypedDict):
    __name__: str
    job: str
    instance: str

class PromethusInstantResult(TypedDict):
    metric: PrometheusMetric
    value: List[float | str]

async def get_prometheus_series(query: str, duration_s: int, step_s: int = 20):
    url = os.getenv("PROMETHEUS_URL")
    now = time.time()
    start = now - duration_s
    async with aiohttp.ClientSession() as session:
        r = await session.get(
            f"{url}/api/v1/query_range?query={query}&start={start}&end={now}&step={step_s}"
        )
        result = await r.json()
    data = result['data']['result']
    return data
    
async def get_prometheus_instant(query: str) -> List[PromethusInstantResult] | None:
    url = os.getenv("PROMETHEUS_URL")
    async with aiohttp.ClientSession() as session:
        r = await session.get(
            f"{url}/api/v1/query?query={query}"
        )
        result = await r.json()
    data = result['data']['result']
    return data
