import time
from typing import List, TypedDict

from .clients.session import get_session
from .config import get_settings

class PrometheusMetric(TypedDict):
    __name__: str
    job: str
    instance: str

class PromethusInstantResult(TypedDict):
    metric: PrometheusMetric
    value: List[float | str]

async def get_prometheus_series(query: str, duration_s: int, step_s: int = 20):
    url = get_settings().prometheus_url
    now = time.time()
    start = now - duration_s
    async with get_session().get(
        f"{url}/api/v1/query_range?query={query}&start={start}&end={now}&step={step_s}"
    ) as r:
        result = await r.json()
    data = result['data']['result']
    return data
    
async def get_prometheus_instant(query: str) -> List[PromethusInstantResult] | None:
    url = get_settings().prometheus_url
    async with get_session().get(
        f"{url}/api/v1/query?query={query}"
    ) as r:
        result = await r.json()
    data = result['data']['result']
    return data
