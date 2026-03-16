import datetime
from bot.ravenfall_query import RavenfallClient
from bot.ravenfall_middleman import (
    MiddlemanClient, StreamMessageType, RavenfallProcessorMessage, RavenfallMessage, Recipient,
    RavenfallStreamMessage, RavenBotStreamMessage, MessageProcessorServer, RavenBotProcessorMessage
)

client = RavenfallClient("http://pc3-server/rf_query/1")
middleman_client = MiddlemanClient("http://127.0.0.1:7101")
processor = MessageProcessorServer(host="127.0.0.1", port=7100)

import asyncio

async def recieve_rf_msg(msg: RavenfallStreamMessage):
    print(msg)

async def recieve_rb_msg(msg: RavenBotStreamMessage):
    print(msg)

async def process_rf_msg(msg: RavenfallProcessorMessage):
    # msg.message.format = "Message modified by processor."
    print(msg)

async def process_rb_msg(msg: RavenBotProcessorMessage):
    print(msg)

async def main():
    # session = await client.get_config()
    # print(session)
    # error_query = await client._query_type(query="select name from players", out_type=dict)
    # print(error_query)

    # middleman_client.add_ravenbot_message_hook(recieve_rb_msg)
    # middleman_client.add_ravenfall_message_hook(recieve_rf_msg)
    # await middleman_client.connect_websocket()
    # print("Connected")
    processor.add_ravenbot_message_hook(process_rb_msg)
    processor.add_ravenfall_message_hook(process_rf_msg)
    await processor.start()

    while True:
        await asyncio.sleep(9999)

asyncio.run(main())