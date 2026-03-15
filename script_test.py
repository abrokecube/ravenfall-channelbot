from bot.ravenfall_query import RavenfallClient
from bot.ravenfall_middleman import MiddlemanClient, StreamMessageType

client = RavenfallClient("http://pc3-server/rf_query/1")
middleman_client = MiddlemanClient("http://127.0.0.1:7101")

import asyncio

async def recieve_msg(msg: StreamMessageType):
    print(msg)

async def main():
    # session = await client.get_config()
    # print(session)
    # error_query = await client._query_type(query="select name from players", out_type=dict)
    # print(error_query)

    middleman_client.add_message_hook(recieve_msg)
    await middleman_client.connect_websocket()
    print("Connected")
    
    while True:
        await asyncio.sleep(9999)

asyncio.run(main())