from aiohttp.client_reqrep import ClientResponse


from aiohttp.client_reqrep import ClientResponse


import time
import aiohttp
from typing import TypedDict
import os

class PrometheusMetric(TypedDict, extra_items=str):
    __name__: str
    job: str
    instance: str

class PromethusInstantResult(TypedDict):
    metric: PrometheusMetric
    value: list[float | str]

class PrometheusSeriesResult(TypedDict):
    metric: PrometheusMetric
    values: list[list[float | str]]

async def get_prometheus_series(query: str, duration_s: int, step_s: int = 20) -> list[PrometheusSeriesResult]:
    url: str | None = os.getenv("PROMETHEUS_URL")
    now: float = time.time()
    start: float = now - duration_s
    async with aiohttp.ClientSession() as session:
        r: ClientResponse = await session.get(
            f"{url}/api/v1/query_range?query={query}&start={start}&end={now}&step={step_s}"
        )
        result = await r.json()
    data = result['data']['result']
    return data
    
async def get_prometheus_instant(query: str) -> list[PromethusInstantResult]:
    url: str | None = os.getenv("PROMETHEUS_URL")
    async with aiohttp.ClientSession() as session:
        r: ClientResponse = await session.get(
            f"{url}/api/v1/query?query={query}"
        )
        result = await r.json()
    data = result['data']['result']
    return data
