"""Run with: python -m unittest tests.test_middleman_api

The middleman is faked. Requests and replies use the keys in the Go source of
ravenfall-middleman 7e5d659 (types.go, socketproxy.go, processor_connector.go).
"""

import json
import unittest
from unittest import mock

from bot import middleman, ravenfallchannel
from bot.clients.session import close_session
from bot.message_templates import RavenBotTemplates
from bot.messageprocessor import MessageProcessor
from bot.ravenfallmanager import RFChannelManager
from tests.test_channel_bugfixes import chat_message, make_town, set_env


# Fields of APIRequest in types.go. The Go decoder ignores anything else.
API_REQUEST_KEYS = {"connectionId", "data", "timeout", "expectedCount"}


def api_returning(body: dict, status: int = 200):
    return mock.patch.object(middleman, "_call_middleman_api", mock.AsyncMock(return_value=(body, status)))


class Requests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoints_and_keys(self):
        cases = [
            (middleman.force_reconnect("rf_town", 60), "/api/reconnect"),
            (middleman.ensure_connected("rf_town", 60), "/api/ensure-connected"),
            (middleman.send_to_client("rf_town", "{}"), "/api/send-to-client"),
            (middleman.send_to_server("rf_town", "{}"), "/api/send-to-server"),
            (middleman.send_to_server_and_wait_response("rf_town", "{}"), "/api/send-and-wait-response"),
        ]
        for call, endpoint in cases:
            with self.subTest(endpoint), api_returning({"success": True}) as api:
                await call
                sent_endpoint, method, body = api.await_args.args
                self.assertEqual((sent_endpoint, method), (endpoint, "POST"))
                self.assertEqual(body["connectionId"], "rf_town")
                self.assertLessEqual(set(body), API_REQUEST_KEYS)

    async def test_user_chosen_connection_id_is_escaped_in_the_status_url(self):
        with api_returning({"success": False, "error": "Connection not found"}, 404) as api:
            await middleman.get_connection_status("town&1 #a")
        self.assertEqual(api.await_args.args[0], "/api/connection-status?connectionId=town%261%20%23a")

    async def test_failed_request_can_be_read_like_a_middleman_error(self):
        self.addAsyncCleanup(close_session)
        set_env(self, RF_MIDDLEMAN_HOST="127.0.0.1", RF_MIDDLEMAN_PORT="1")
        with self.assertLogs("middleman", "ERROR"):
            response = await middleman.send_to_server_and_wait_response("rf_town", "{}")
        self.assertIs(response["success"], False)
        self.assertIn("error", response)


class Replies(unittest.IsolatedAsyncioTestCase):
    async def test_connection_status(self):
        body = {
            "success": True,
            "status": {
                "clientConnected": True,
                "serverConnected": False,
                "timeUntilClose": -1,
                "connectionId": "rf_town",
            },
        }
        with api_returning(body):
            status, err = await middleman.get_connection_status("rf_town")
        self.assertIsNone(err)
        self.assertEqual(status, middleman.ConnectionStatus("rf_town", True, False, -1))

    async def test_unknown_connection(self):
        with api_returning({"success": False, "error": "Connection not found"}, 404):
            status, err = await middleman.get_connection_status("rf_other")
        self.assertIsNone(status)
        self.assertEqual(err, "Connection not found")

    async def test_power_saving_comes_from_the_config(self):
        for disable_timeout, power_saving in ((True, False), (False, True)):
            manager = RFChannelManager.__new__(RFChannelManager)
            manager.middleman_processor_server_client_count = 0
            manager.middleman_power_saving = None
            body = {
                "success": True,
                "config": {
                    "disableTimeout": disable_timeout,
                    "proxyMappings": [{"connectionId": "rf_town"}],
                    "identifierTimeouts": {},
                    "messageProcessor": {"enabled": True, "urls": ["ws://127.0.0.1:7100/process"]},
                },
            }
            with self.subTest(disable_timeout), api_returning(body):
                await manager.on_processor_connect(mock.Mock())
                self.assertIs(manager.middleman_power_saving, power_saving)
                self.assertTrue(manager.middleman_connected)


class Inspect(unittest.IsolatedAsyncioTestCase):
    async def test_correlation_id_is_inside_the_message(self):
        """The middleman takes it from CorrelationId in `data`, and matches replies on it."""
        town = make_town()
        reply = {"Identifier": "message", "CorrelationId": "message-id"}
        result = {"success": True, "responses": [reply], "timeout": False}
        with api_returning(result) as api, mock.patch.object(ravenfallchannel, "send_to_client", mock.AsyncMock()) as to_client:
            await town.event_twitch_message(chat_message("!inspect"))
        body = api.await_args.args[2]
        self.assertEqual(json.loads(body["data"])["CorrelationId"], "message-id")
        self.assertNotIn("correlationId", body)
        to_client.assert_awaited_once_with("rf_town", json.dumps(reply))

    async def test_template_always_has_a_correlation_id(self):
        self.assertTrue(json.loads(RavenBotTemplates.inspect("viewer"))["CorrelationId"])


class Processor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.processor = MessageProcessor()
        self.seen = []

        async def callback(_message, metadata, _client):
            self.seen.append(metadata)
            return self.reply

        self.reply = None
        self.processor.add_message_callback(callback)

    async def process(self, **wrapper):
        response = await self.processor.process_message(json.dumps(wrapper), mock.Mock())
        return json.loads(response)

    async def test_reads_the_addresses_and_time(self):
        await self.process(
            source="SERVER",
            clientAddr="127.0.0.1:1",
            serverAddr="127.0.0.1:2",
            connectionId="rf_town",
            correlationId="id",
            isApi=False,
            timestamp="2026-11-06T02:21:36-06:00",
            originalMessage={"a": 1},
            message={"a": 1},
        )
        metadata = self.seen[0]
        self.assertEqual(metadata.client_addr, "127.0.0.1:1")
        self.assertEqual(metadata.server_addr, "127.0.0.1:2")
        self.assertEqual(metadata.timestamp, "2026-11-06T02:21:36-06:00")

    async def test_message_is_kept_when_a_callback_has_no_change(self):
        response = await self.process(source="API-SERVER", connectionId="rf_town", correlationId="id", message={"a": 1})
        self.assertEqual(response["message"], {"a": 1})
        self.assertEqual(response["correlationId"], "id")

    async def test_failure_after_parsing_still_reaches_the_waiting_middleman(self):
        """Without the correlation id the middleman waits out its 5 second timeout."""
        self.reply = {"message": {"bad": object()}}
        with self.assertLogs("new_message_processor", "ERROR"):
            response = await self.process(source="CLIENT", connectionId="rf_town", correlationId="id", message={})
        self.assertEqual(response["correlationId"], "id")
        self.assertIn("error", response)


if __name__ == "__main__":
    unittest.main()
