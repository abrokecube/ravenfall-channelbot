"""Run with: python -m unittest tests.test_clients

The services are fake aiohttp servers on localhost, so these go through real sockets.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestServer

from bot import middleman, multichat_command, ravenfallchannel, ravenfallmanager
from bot.clients import beefweb
from bot.clients import session as session_module
from bot.clients.query import QueryClient
from bot.clients.session import close_session, get_session
from bot.cogs.music import format_now_playing
from bot.config import ConfigError
from bot.models import parse_boost
from bot.process_watchdog_client import ProcessWatcherClient
from bot.ravenfallmanager import RFChannelManager
from bot.rf_webops_client import WebOpsClient
from tests.test_channel_bugfixes import make_town, set_env


class ClientTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.addAsyncCleanup(close_session)
        self.requests = []

    async def serve(self, handler) -> TestServer:
        async def record(request: web.Request):
            self.requests.append(request)
            return await handler(request)

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", record)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return server


async def session_on_another_loop():
    """Gets a session the way a twitchAPI callback does, from its own thread and loop."""

    async def fetch():
        return get_session()

    return await asyncio.to_thread(asyncio.run, fetch())


class SharedSession(ClientTestCase):
    async def test_same_session_until_closed(self):
        session = get_session()
        self.assertIs(get_session(), session)
        await close_session()
        self.assertTrue(session.closed)
        self.assertIsNot(get_session(), session)

    async def test_closing_with_no_session_is_fine(self):
        await close_session()
        await close_session()

    async def test_no_connection_limit(self):
        self.assertEqual(get_session().connector.limit, 0)

    async def test_each_request_gets_its_own_connection(self):
        async def handler(request):
            return web.json_response({"peer": request.transport.get_extra_info("peername")[1]})

        server = await self.serve(handler)
        client = QueryClient(str(server.make_url("")))
        first = await client.get("select * from session")
        second = await client.get("select * from session")
        self.assertNotEqual(first["peer"], second["peer"])

    async def test_cookies_are_not_kept(self):
        async def handler(request):
            response = web.json_response({"cookies": dict(request.cookies)})
            response.set_cookie("seen", "1")
            return response

        server = await self.serve(handler)
        client = QueryClient(str(server.make_url("")))
        await client.get("first")
        self.assertEqual(await client.get("second"), {"cookies": {}})

    async def test_each_loop_has_its_own_session(self):
        session = get_session()
        other = await session_on_another_loop()
        self.assertIsNot(other, session)
        self.assertFalse(session.closed)
        self.assertIs(get_session(), session)

    async def test_another_loop_does_not_break_a_request_in_flight(self):
        async def handler(request):
            return web.json_response({"ok": True})

        server = await self.serve(handler)
        connector = get_session().connector
        connect = connector.connect

        async def connect_while_another_loop_runs(*args, **kwargs):
            await session_on_another_loop()
            return await connect(*args, **kwargs)

        with mock.patch.object(connector, "connect", connect_while_another_loop_runs):
            client = QueryClient(str(server.make_url("")))
            self.assertEqual(await client.get("select * from session"), {"ok": True})

    async def test_close_detaches_sessions_from_other_loops(self):
        other = await session_on_another_loop()
        get_session()
        await close_session()
        self.assertTrue(other.closed)
        self.assertEqual(session_module._sessions, {})

    async def test_session_from_a_finished_loop_is_dropped(self):
        other = await session_on_another_loop()
        get_session()
        self.assertTrue(other.closed)
        self.assertEqual(len(session_module._sessions), 1)


class Queries(ClientTestCase):
    async def test_query_goes_in_the_path(self):
        async def handler(request):
            return web.json_response({"path": request.path})

        server = await self.serve(handler)
        client = QueryClient(str(server.make_url("/ravenfall")) + "/")
        self.assertEqual(await client.get("select * from session"), {"path": "/ravenfall/select * from session"})

    async def test_refused_connection_is_none(self):
        with self.assertLogs("bot.clients.query", "ERROR"):
            self.assertIsNone(await QueryClient("http://127.0.0.1:1").get("select * from session"))

    async def test_timeout_is_none_and_can_be_quiet(self):
        release = asyncio.Event()

        async def handler(request):
            await release.wait()
            return web.json_response({})

        server = await self.serve(handler)
        self.addCleanup(release.set)
        client = QueryClient(str(server.make_url("")))
        with self.assertLogs("bot.clients.query", "ERROR"):
            self.assertIsNone(await client.get("slow", timeout=0.05))
        with self.assertNoLogs("bot.clients.query", "ERROR"):
            self.assertIsNone(await client.get("slow", timeout=0.05, suppress_timeout_error=True))
        release.set()

    async def test_reply_that_is_not_json_is_none(self):
        async def handler(request):
            return web.Response(text="nope")

        server = await self.serve(handler)
        with self.assertLogs("bot.clients.query", "ERROR"):
            self.assertIsNone(await QueryClient(str(server.make_url(""))).get("select * from session"))


class Multichat(ClientTestCase):
    async def use_server(self, handler):
        server = await self.serve(handler)
        set_env(
            self,
            MULTICHAT_COMMAND_SERVER_HOST=server.host,
            MULTICHAT_COMMAND_SERVER_PORT=str(server.port),
        )

    async def test_command(self):
        async def handler(request):
            return web.json_response({"ok": True, "body": await request.json()}, status=202)

        await self.use_server(handler)
        result = await multichat_command.send_multichat_command("?ping", "1", "user", "2", "town")
        self.assertEqual(self.requests[0].path, "/command")
        self.assertEqual(result["status"], 202)
        self.assertEqual(result["data"]["body"], {
            "text": "?ping", "user_id": "1", "user_name": "user", "channel_id": "2", "channel_name": "town",
        })

    async def test_item_tracking(self):
        async def handler(request):
            return web.json_response(await request.json())

        await self.use_server(handler)
        result = await multichat_command.track_item_use("user", 1, "item", 5)
        self.assertEqual(self.requests[0].path, "/track_item_use")
        self.assertEqual(result, {
            "status": 200,
            "data": {"user_name": "user", "char_index": 1, "item_id": "item", "amount": 5},
        })

    async def test_reads_unwrap_data_except_scrolls(self):
        async def handler(request):
            return web.json_response({"data": request.path})

        await self.use_server(handler)
        cases = [
            (multichat_command.get_desync_info(), "/get_desync"),
            (multichat_command.get_total_item_count(), "/get_total_item_count"),
            (multichat_command.get_char_info(), "/get_char_data"),
            (multichat_command.get_char_items("7"), "/get_char_items/7"),
            (multichat_command.get_char_coins("7"), "/get_char_coins/7"),
        ]
        for call, path in cases:
            with self.subTest(path):
                self.assertEqual(await call, {"status": 200, "data": path})
        self.assertEqual(
            await multichat_command.get_scroll_counts("7"),
            {"status": 200, "data": {"data": "/get_scrolls/7"}},
        )

    async def test_failures_are_status_500(self):
        set_env(self, MULTICHAT_COMMAND_SERVER_HOST="127.0.0.1", MULTICHAT_COMMAND_SERVER_PORT="1")
        with self.assertLogs("bot.multichat_command", "ERROR"):
            sent = await multichat_command.send_multichat_command("?ping")
        self.assertEqual(sent["status"], 500)
        self.assertIn("Failed to send command", sent["error"])
        fetched = await multichat_command.get_desync_info()
        self.assertEqual(fetched["status"], 500)
        self.assertIn("Failed to fetch", fetched["error"])


class Middleman(ClientTestCase):
    async def use_server(self, handler):
        server = await self.serve(handler)
        set_env(self, RF_MIDDLEMAN_HOST=server.host, RF_MIDDLEMAN_PORT=str(server.port))

    async def test_post_returns_the_reply_of_either_build(self):
        replies = [
            {"success": True, "correlationId": "a", "responses": []},
            {"success": False, "error": "Connection not found"},
        ]

        async def handler(request):
            return web.json_response(replies[len(self.requests) - 1])

        await self.use_server(handler)
        self.assertEqual(await middleman.send_to_server_and_wait_response("rf", "{}"), replies[0])
        self.assertEqual(await middleman.send_to_client("rf", "{}"), replies[1])
        self.assertEqual(self.requests[0].path, "/api/send-and-wait-response")
        self.assertEqual(self.requests[1].path, "/api/send-to-client")

    async def test_connection_status(self):
        async def handler(request):
            return web.json_response({
                "success": True,
                "status": {"connectionId": "rf", "clientConnected": True, "serverConnected": False, "timeUntilClose": 5},
            })

        await self.use_server(handler)
        status, error = await middleman.get_connection_status("rf")
        self.assertIsNone(error)
        self.assertEqual(self.requests[0].method, "GET")
        self.assertEqual(self.requests[0].query["connectionId"], "rf")
        self.assertEqual(status, middleman.ConnectionStatus("rf", True, False, 5))

    async def test_unset_address_reads_like_a_middleman_error(self):
        set_env(self, RF_MIDDLEMAN_HOST="", RF_MIDDLEMAN_PORT="")
        with self.assertLogs("middleman", "ERROR"):
            response = await middleman.send_to_server("rf", "{}")
        self.assertIs(response["success"], False)


class OtherClients(ClientTestCase):
    async def test_watchdog(self):
        async def handler(request):
            return web.json_response({"path": request.path, "method": request.method})

        server = await self.serve(handler)
        client = ProcessWatcherClient(str(server.make_url("")))
        self.assertEqual(await client.get_processes(), {"path": "/processes", "method": "GET"})
        cases = [
            (client.start_process("a"), "/processes/a/start"),
            (client.stop_process("a"), "/processes/a/stop"),
            (client.restart_process("a"), "/processes/a/restart"),
            (client.git_pull("a"), "/processes/a/git-pull"),
            (client.reload_config(), "/config/reload"),
        ]
        for call, path in cases:
            with self.subTest(path):
                self.assertEqual(await call, {"path": path, "method": "POST"})

    async def test_webops_failure_raises_with_the_body(self):
        async def handler(request):
            return web.Response(text="broken", status=500)

        server = await self.serve(handler)
        client = WebOpsClient(str(server.make_url("")))
        with self.assertRaisesRegex(Exception, "Redemption failed: 500 - broken"):
            await client.redeem_items("item", 1, [])
        with self.assertRaisesRegex(Exception, "Failed to get points: 500 - broken"):
            await client.get_total_loyalty_points(["a"])


class ChannelHelpers(unittest.IsolatedAsyncioTestCase):
    def town(self):
        town = make_town()
        town.channel_id = "1"
        town.channel_name = "town"
        town.send_chat_message = mock.AsyncMock()
        return town

    async def test_multichat_sends_as_the_town(self):
        town = self.town()
        send = mock.AsyncMock(return_value={"status": 200})
        with mock.patch.object(ravenfallchannel, "send_multichat_command", send):
            await town.multichat("?resync")
        send.assert_awaited_once_with(
            text="?resync", user_id="1", user_name="town", channel_id="1", channel_name="town",
        )
        town.send_chat_message.assert_not_awaited()

    async def test_multichat_falls_back_to_chat(self):
        town = self.town()
        send = mock.AsyncMock(return_value={"status": 500, "error": "down"})
        with mock.patch.object(ravenfallchannel, "send_multichat_command", send):
            await town.multichat("?resync")
        town.send_chat_message.assert_awaited_once_with("?resync")

    async def test_get_player_queries_by_name(self):
        town = self.town()
        town.get_query = mock.AsyncMock(return_value={"id": "a"})
        self.assertEqual(await town.get_player("viewer"), {"id": "a"})
        town.get_query.assert_awaited_once_with("select * from players where name = 'viewer'")


class PreRestart(unittest.IsolatedAsyncioTestCase):
    def town(self, players):
        town = make_town()
        town.manager.middleman_connected = True
        town.get_query = mock.AsyncMock(return_value=players)
        town.multichat = mock.AsyncMock()
        return town

    async def test_game_offline_skips_the_rest(self):
        town = self.town(None)
        await town._ravenfall_pre_restart()
        town.multichat.assert_not_awaited()

    async def test_a_reply_that_is_not_a_list_skips_the_rest(self):
        for reply in ("Failed to parse the query",):
            with self.subTest(reply):
                town = self.town(reply)
                await town._ravenfall_pre_restart()
                town.multichat.assert_not_awaited()

    async def test_no_players_continues(self):
        session = mock.MagicMock()
        session.__aenter__ = mock.AsyncMock(return_value=session)
        session.__aexit__ = mock.AsyncMock(return_value=False)
        result = mock.MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute = mock.AsyncMock(return_value=result)
        for reply in ({}, []):
            with self.subTest(reply), mock.patch.object(ravenfallchannel, "get_async_session", return_value=session):
                town = self.town(reply)
                await town._ravenfall_pre_restart()
                town.multichat.assert_awaited_once_with("?randleave")

    async def test_without_the_middleman_it_still_leaves(self):
        town = self.town(None)
        town.manager.middleman_connected = False
        await town._ravenfall_pre_restart()
        town.get_query.assert_not_awaited()
        town.multichat.assert_awaited_once_with("?randleave")


class Boosts(unittest.TestCase):
    def test_parse_boost(self):
        self.assertEqual(parse_boost("Attack 50%"), ("Attack", 50.0))
        self.assertEqual(parse_boost("Attack 12.5"), ("Attack", 12.5))
        self.assertIsNone(parse_boost(""))
        self.assertIsNone(parse_boost("   "))
        self.assertIsNone(parse_boost("Attack"))


class BoxPids(unittest.IsolatedAsyncioTestCase):
    async def test_list_box_pids(self):
        manager = RFChannelManager.__new__(RFChannelManager)
        manager.channels = [
            SimpleNamespace(channel_name="a", sandboxie_box="Box0"),
            SimpleNamespace(channel_name="b", sandboxie_box="Box1"),
        ]
        set_env(self, SANDBOXIE_START_PATH="C:/Start.exe")

        async def runshell(command):
            return 0, "2\n11\n12\n" if "/box:Box0 " in command else "1\n21\n"

        with mock.patch.object(ravenfallmanager, "runshell", mock.AsyncMock(side_effect=runshell)) as shell:
            self.assertEqual(await manager.list_box_pids(), {"a": ["2", "11", "12"], "b": ["1", "21"]})
        shell.assert_any_await('"C:/Start.exe" /box:Box0 /silent /listpids')


class Beefweb(ClientTestCase):
    async def serve_player(self, player: dict) -> None:
        async def handler(request):
            return web.json_response({"player": player})

        server = await self.serve(handler)
        set_env(self, BEEFWEB_URL=str(server.make_url("")).rstrip("/"))

    async def test_playing_song(self):
        await self.serve_player({
            "playbackState": "playing",
            "activeItem": {
                "playlistId": "p1", "index": 3, "position": 83.6, "duration": 225.2,
                "columns": ["Song", "Band", "Record"],
            },
        })
        np = await beefweb.get_now_playing()
        self.assertEqual(np, beefweb.NowPlaying("playing", "Song", "Band", "Record", 83.6, 225.2))
        self.assertEqual(self.requests[0].path, "/api/player")
        self.assertEqual(self.requests[0].query["columns"], "%title%,%artist%,%album%")
        self.assertEqual(format_now_playing(np), "Now playing: Song by Band from Record [1:23 / 3:45]")

    async def test_stopped(self):
        await self.serve_player({
            "playbackState": "stopped",
            "activeItem": {"playlistId": "", "index": -1, "position": 0, "duration": 0, "columns": []},
        })
        np = await beefweb.get_now_playing()
        self.assertEqual(np.state, "stopped")
        self.assertEqual(format_now_playing(np), "Nothing is playing right now.")

    async def test_not_configured(self):
        set_env(self, BEEFWEB_URL="")
        with self.assertRaises(ConfigError):
            await beefweb.get_now_playing()

    def test_format_paused_stream_without_tags(self):
        np = beefweb.NowPlaying("paused", "", "", "", 3725, 0)
        self.assertEqual(format_now_playing(np), "Now playing: Unknown title [1:02:05] (paused)")
