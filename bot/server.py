from aiohttp import web
from .prom_metrics import MetricsManager
from .ravenfallmanager import RFChannelManager
from .chat_system import ChatManager
import logging
import json

LOGGER = logging.getLogger(__name__)

class SomeEndpoints:
    def __init__(self, rfmanager: 'RFChannelManager', chat_manager: 'ChatManager', host: str = '0.0.0.0', port: int = 8080):
        self.rfmanager = rfmanager
        self.chat_manager = chat_manager
        self.host = host
        self.port = port
        self.app = web.Application()
        self.app.add_routes([
            web.get('/metrics', self.handle_metrics),
            web.get('/api/chat/stream', self.handle_stream),
            web.get('/api/chat/rooms/{room}/history', self.handle_history),
            web.post('/api/chat/rooms/{room}/send', self.handle_send),
        ])
        self.metrics_manager = MetricsManager(self.rfmanager)

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        LOGGER.info(f"Endpoints listening on http://{self.host}:{self.port}")

    async def handle_metrics(self, request: web.Request):
        metrics = await self.metrics_manager.get_metrics()
        return web.Response(text=metrics, content_type='text/plain')

    # async def handle_stream(self, request: web.Request):
    #     ws = web.WebSocketResponse()
    #     await ws.prepare(request)
    #     await self.chat_manager.register_websocket(ws)

    #     try:
    #         async for _ in ws:
    #             pass
    #     finally:
    #         await self.chat_manager.unregister_websocket(ws)

    #     return ws

    # async def handle_history(self, request: web.Request):
    #     room_name = request.match_info['room']
    #     room = self.chat_manager.get_room(room_name)
    #     history = await room.get_history()
        
    #     data = []
    #     for msg in history:
    #         m_dict = {
    #             'id': msg.id,
    #             'room': msg.room_name,
    #             'content': msg.content,
    #             'author': msg.author,
    #             'timestamp': msg.timestamp.isoformat()
    #         }
    #         data.append(m_dict)
            
    #     return web.json_response(data)

    # async def handle_send(self, request: web.Request):
    #     room_name = request.match_info['room']
    #     try:
    #         data = await request.json()
    #         author = data.get('author', 'Anonymous')
    #         content = data.get('content')
    #         reply_to_id = data.get('reply_to_id')
    #         auth_key = data.get('auth_key')
            
    #         if not content:
    #             return web.Response(status=400, text="Missing content")

    #         msg = await self.chat_manager.send_message(room_name, author, content, reply_to_id, auth_key)
            
    #         return web.json_response({
    #             'status': 'ok',
    #             'message_id': msg.id
    #         })
    #     except Exception as e:
    #         LOGGER.error(f"Error sending message: {e}")
    #         return web.Response(status=500, text=str(e))
