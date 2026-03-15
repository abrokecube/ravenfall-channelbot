import datetime
from bot.ravenfall_query import RavenfallClient
from bot.ravenfall_middleman import (
    MiddlemanClient, StreamMessageType, RavenfallProcessorMessage, RavenfallMessage, Recipient,
    RavenfallStreamMessage, RavenBotStreamMessage
)

client = RavenfallClient("http://pc3-server/rf_query/1")
middleman_client = MiddlemanClient("http://127.0.0.1:7101")

import asyncio

async def recieve_rf_msg(msg: RavenfallStreamMessage):
    print(msg)

async def recieve_rb_msg(msg: RavenBotStreamMessage):
    print(msg)

async def main():
    # session = await client.get_config()
    # print(session)
    # error_query = await client._query_type(query="select name from players", out_type=dict)
    # print(error_query)

    middleman_client.add_ravenbot_message_hook(recieve_rb_msg)
    middleman_client.add_ravenfall_message_hook(recieve_rf_msg)
    await middleman_client.connect_websocket()
    print("Connected")

    while True:
        await asyncio.sleep(9999)

    # m = RavenfallMessage(
    #         recipient=Recipient(
    #             user_id="test",
    #             character_id="test",
    #             platform="test",
    #             platform_id="test",
    #             platform_user_name="test"
    #         ),
    #         format="test",
    #         args=[],
    #         tags=[],
    #         category="test",
    #         identifier="test",
    #         correlation_id="test"
    #     )

    # aga = RavenfallProcessorMessage(
    #     client_addr="test",
    #     server_addr="test",
    #     connection_id="test",
    #     correlation_id="test",
    #     is_api=False,
    #     timestamp=datetime.datetime.now(),
    #     message=m,
    #     original_message=m
    # )
    # print(aga)

asyncio.run(main())