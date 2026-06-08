from aiohttp import web
from .prom_metrics_old import MetricsManager
from .ravenfallmanager_old import RFChannelManager
import logging

LOGGER = logging.getLogger(__name__)


class SomeEndpoints:
    def __init__(
        self, rfmanager: "RFChannelManager", host: str = "0.0.0.0", port: int = 8080
    ):
        self.rfmanager: "RFChannelManager" = rfmanager
        self.host: str = host
        self.port: int = port
        self.app: web.Application = web.Application()
        _ = self.app.add_routes(
            [
                web.get("/metrics", self.handle_metrics),
            ]
        )
        self.metrics_manager: MetricsManager = MetricsManager(self.rfmanager)

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        LOGGER.info(f"Endpoints listening on http://{self.host}:{self.port}")

    async def handle_metrics(self, _: web.Request):
        metrics = await self.metrics_manager.get_metrics()
        return web.Response(text=metrics, content_type="text/plain")
