import asyncio
from utils.websocket_client import AutoReconnectingWebSocket


URL = "ws://127.0.0.1:7110/api/chat/stream"

async def on_message(msg):
    print(msg)
    
async def on_connect():
    print("connected")

async def on_disconnect():
    print("disconnected")
    
async def on_error(err):
    print(err)

a = AutoReconnectingWebSocket(URL, on_message, on_connect, on_disconnect, on_error)

async def main():
    await a.connect()
    while True:
        await asyncio.sleep(999999999)
    
if __name__ == "__main__":
    asyncio.run(main())