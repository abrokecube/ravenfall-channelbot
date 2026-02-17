import asyncio
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Dict, Set, TYPE_CHECKING
from aiohttp import web

from database.models import ChatMessage
from database.session import get_async_session
from sqlalchemy import select

if TYPE_CHECKING:
    from .commands import Commands

logger = logging.getLogger(__name__)

@dataclass
class Message:
    id: int
    room_name: str
    content: str
    author: str
    timestamp: datetime
    reply_to_id: int = None
    user_id: str = None

class ChatRoom:
    def __init__(self, name: str):
        self.name = name
        self.connected_users: Set[str] = set() # Just tracking usernames for now if needed

    async def get_history(self, limit: int = 50) -> List[Message]:
        async with get_async_session() as session:
            stmt = select(ChatMessage).where(ChatMessage.room_name == self.name).order_by(ChatMessage.timestamp.desc()).limit(limit)
            result = await session.execute(stmt)
            messages = result.scalars().all()
            
            return [
                Message(
                    id=m.id,
                    room_name=m.room_name,
                    content=m.content,
                    author=m.user_name,
                    timestamp=m.timestamp,
                    reply_to_id=m.reply_to_id,
                    user_id=m.user_id
                ) for m in reversed(messages) # Return in chronological order
            ]

class ChatManager:
    def __init__(self, commands_bot: 'Commands'):
        self.rooms: Dict[str, ChatRoom] = {}
        self.websockets: Set[web.WebSocketResponse] = set()
        self.bot: Commands = commands_bot
        self.admin_key = None

    def get_room(self, room_name: str) -> ChatRoom:
        if room_name not in self.rooms:
            self.rooms[room_name] = ChatRoom(room_name)
        return self.rooms[room_name]

    def get_user_id_from_key(self, key: str) -> str:
        if self.admin_key is not None and key == self.admin_key:
            return "admin"

    async def send_message(
        self, room_name: str, author: str, content: str, 
        reply_to_id: int = None, auth_key: str = None
    ) -> Message:
        timestamp = datetime.now(tz=timezone.utc)
        
        user_id = self.get_user_id_from_key(auth_key)

        # Persist to DB
        async with get_async_session() as session:
            db_msg = ChatMessage(
                room_name=room_name,
                user_name=author,
                content=content,
                timestamp=timestamp,
                reply_to_id=reply_to_id,
                user_id=user_id,
            )
            session.add(db_msg)
            await session.flush()
            message_id = db_msg.id

        message = Message(
            id=message_id,
            room_name=room_name,
            content=content,
            author=author,
            timestamp=timestamp,
            reply_to_id=reply_to_id,
            user_id=user_id,
        )

        # Broadcast to WebSockets
        await self.broadcast(message)

        # Process commands if it's not from the bot itself
        if author != "Bot":
            asyncio.create_task(self.process_command(message))

        return message

    async def broadcast(self, message: Message):
        data = asdict(message)
        data['timestamp'] = data['timestamp'].isoformat()
        
        to_remove = set()
        for ws in self.websockets:
            try:
                await ws.send_json({'type': 'message', 'data': data})
            except Exception:
                to_remove.add(ws)
        
        for ws in to_remove:
            self.websockets.remove(ws)

    async def process_command(self, message: Message):        
        ctx = ServerContext(message,  self)
        await self.bot.process_chat_message(ctx)


    async def register_websocket(self, ws: web.WebSocketResponse):
        self.websockets.add(ws)

    async def unregister_websocket(self, ws: web.WebSocketResponse):
        self.websockets.discard(ws)
