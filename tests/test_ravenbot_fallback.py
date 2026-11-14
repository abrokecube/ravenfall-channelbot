"""The bot posting the game messages RavenBot drops at its chat limit (bot/ravenbotfallback.py)."""

import unittest
from types import SimpleNamespace
from unittest import mock

from bot.messageprocessor import MessageMetadata
from bot.ravenbotfallback import RavenBotChatFallback, normalize, render, split_message
from bot.ravenfallmanager import RFChannelManager
from tests.test_channel_bugfixes import chat_message, make_town, set_env

CID = "3f2b8c1e-0d4a-4b7e-9a51-6c2d8e9f0a1b"


def game_message(fmt, args=(), platform="twitch", user="alice", cid=""):
    return {
        "Identifier": "message",
        "Recipent": {"Platform": platform, "PlatformUserName": user, "PlatformId": "1"},
        "Format": fmt,
        "Args": list(args),
        "CorrelationId": cid,
    }


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Rendering(unittest.TestCase):
    def test_arguments_fill_in_order(self):
        self.assertEqual(render("{player} found {item}!", ["alice", "a sword"]), "alice found a sword!")

    def test_missing_argument_leaves_its_name(self):
        self.assertEqual(render("{a} and {b}", ["x"]), "x and b")

    def test_meta_prefix_is_removed(self):
        self.assertEqual(render("%[category:raid]%%[tags:start]%Raid started", []), "Raid started")

    def test_whole_floats_and_bools_print_like_csharp(self):
        self.assertEqual(render("{a} {b}", [2.0, True]), "2 True")

    def test_long_messages_split_at_spaces(self):
        parts = split_message("word " * 200)
        self.assertTrue(all(len(p) <= 499 for p in parts))
        self.assertEqual(" ".join(parts).split(), ["word"] * 200)

    def test_normalize_ignores_case_spacing_and_mention(self):
        self.assertEqual(normalize("@Alice,  You   WIN"), normalize("you win"))


class Fallback(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.send = mock.AsyncMock()
        self.fallback = RavenBotChatFallback(self.send, ("!",), clock=self.clock)

    async def advance(self, seconds):
        self.clock.now += seconds
        return await self.fallback.flush()

    async def test_message_ravenbot_posts_is_not_resent(self):
        self.fallback.expect(game_message("You gained {xp} exp", [5]))
        self.assertTrue(self.fallback.seen("@alice, You gained 5 exp"))
        self.assertEqual(await self.advance(2), 0)
        self.send.assert_not_awaited()

    async def test_dropped_message_waits_for_the_delay(self):
        self.fallback.expect(game_message("You gained {xp} exp", [5]))
        self.assertEqual(await self.advance(1.0), 0)
        self.assertEqual(await self.advance(0.5), 1)
        self.send.assert_awaited_once_with("@alice, You gained 5 exp", None)

    async def test_reply_is_threaded_to_the_command(self):
        self.fallback.expect(game_message("You have 10 coins", cid=CID))
        await self.advance(2)
        self.send.assert_awaited_once_with("You have 10 coins", CID)

    async def test_correlation_id_that_is_no_message_id_gets_a_mention(self):
        self.fallback.expect(game_message("You have 10 coins", cid="server-request"))
        await self.advance(2)
        self.send.assert_awaited_once_with("@alice, You have 10 coins", None)

    async def test_system_message_is_plain(self):
        self.fallback.expect(game_message("A raid boss appeared", platform="system", user=""))
        await self.advance(2)
        self.send.assert_awaited_once_with("A raid boss appeared", None)

    async def test_other_platforms_and_non_messages_are_ignored(self):
        self.fallback.expect(game_message("hi", platform="discord"))
        self.fallback.expect({"Identifier": "session", "Format": "x"})
        self.fallback.expect(game_message("   "))
        await self.advance(2)
        self.send.assert_not_awaited()

    async def test_old_messages_are_let_go(self):
        self.fallback.expect(game_message("Too late"))
        await self.advance(61)
        self.send.assert_not_awaited()
        self.assertEqual(self.fallback.expired_count, 1)

    async def test_text_that_looks_like_a_command_is_not_sent(self):
        self.fallback.expect(game_message("!join to play", platform="system", user=""))
        await self.advance(2)
        self.send.assert_not_awaited()

    async def test_each_part_of_a_long_message_is_matched_on_its_own(self):
        self.fallback.expect(game_message("word " * 150, platform="system", user=""))
        first = split_message(("word " * 150).strip())[0]
        self.fallback.seen(first)
        await self.advance(2)
        self.assertEqual(self.send.await_count, 1)

    async def test_argument_formatted_differently_still_matches(self):
        self.fallback.expect(game_message("You found a treasure chest in the old ruins worth {v} coins", [1.5]))
        self.assertTrue(self.fallback.seen("@alice, You found a treasure chest in the old ruins worth 1,5 coins"))

    async def test_short_texts_need_to_match_exactly(self):
        self.fallback.expect(game_message("Yes", platform="system", user=""))
        self.assertFalse(self.fallback.seen("No"))

    async def test_ravenbot_posting_after_the_resend_is_counted(self):
        self.fallback.expect(game_message("Slow message"))
        await self.advance(2)
        self.assertTrue(self.fallback.seen("@alice, Slow message"))
        self.assertEqual(self.fallback.duplicate_count, 1)

    async def test_failed_send_does_not_stop_the_rest(self):
        self.send.side_effect = [Exception("down"), None]
        self.fallback.expect(game_message("one", platform="system", user=""))
        self.fallback.expect(game_message("two", platform="system", user=""))
        self.assertEqual(await self.advance(2), 1)


class Wiring(unittest.IsolatedAsyncioTestCase):
    async def handle(self, source, out_message):
        town = SimpleNamespace(
            middleman_connection_id="rf_town",
            event_ravenfall_message=mock.AsyncMock(),
            process_ravenfall_message=mock.AsyncMock(return_value=out_message),
            expect_ravenbot_chat=mock.Mock(),
        )
        manager = RFChannelManager.__new__(RFChannelManager)
        manager.channels = [town]
        metadata = MessageMetadata(source=source, connection_id="rf_town", is_api=source.startswith("API"))
        await manager.handle_processor_message(game_message("x"), metadata, mock.Mock())
        return town.expect_ravenbot_chat

    async def test_game_messages_are_tracked(self):
        out = game_message("translated")
        (await self.handle("SERVER", out)).assert_called_once_with(out)

    async def test_api_messages_to_ravenbot_are_tracked(self):
        (await self.handle("API-SERVER", None)).assert_called_once()

    async def test_blocked_messages_are_not_tracked(self):
        (await self.handle("SERVER", {"block": True})).assert_not_called()

    async def test_ravenbot_to_game_is_not_tracked(self):
        town = SimpleNamespace(
            middleman_connection_id="rf_town",
            event_ravenbot_message=mock.AsyncMock(),
            process_ravenbot_message=mock.AsyncMock(return_value={"Identifier": "join"}),
            expect_ravenbot_chat=mock.Mock(),
        )
        manager = RFChannelManager.__new__(RFChannelManager)
        manager.channels = [town]
        await manager.handle_processor_message({}, MessageMetadata(source="CLIENT", connection_id="rf_town"), mock.Mock())
        town.expect_ravenbot_chat.assert_not_called()

    async def test_ravenbot_chat_clears_pending_messages(self):
        set_env(self, RAVENBOT_USER_ID="99", BOT_USER_ID="1")
        town = make_town()
        town.ravenbot_fallback = mock.Mock()
        message = chat_message("@alice, hello")
        message.user.id = "99"
        await town.event_twitch_message(message)
        town.ravenbot_fallback.seen.assert_called_once_with("@alice, hello")

    async def test_other_chatters_are_not_matched(self):
        set_env(self, RAVENBOT_USER_ID="99", BOT_USER_ID="1")
        town = make_town()
        town.ravenbot_fallback = mock.Mock()
        await town.event_twitch_message(chat_message("@alice, hello"))
        town.ravenbot_fallback.seen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
