from bot.ravenfall_query import RavenfallClient

client = RavenfallClient("http://pc3-server/rf_query/1")


import asyncio

async def main():
    session = await client.get_config()
    print(session)
    # error_query = await client._query_type(query="select name from players", out_type=dict)
    # print(error_query)

asyncio.run(main())