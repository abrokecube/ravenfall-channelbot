"""Run with: python -m unittest tests.test_command_framework"""

import unittest
from unittest import mock

from bot.commands.checks import HasRole, TwitchOnly
from bot.commands.cooldown import Cooldown
from bot.commands.decorators import checks
from bot.commands.enums import BucketType, UserRole
from bot.commands.events import CommandEvent, MessageEvent, TwitchMessageEvent


def make_message(user: str = "user1", room: str = "room1") -> MessageEvent:
    return MessageEvent(
        data={},
        text="!test",
        id="message",
        author_login=user,
        author_name=user,
        author_id=user,
        author_roles={UserRole.USER},
        room_name=room,
        room_id=room,
        room_capabilities=None,
        bot_user_login="bot",
        bot_user_name="bot",
        bot_user_id="bot",
    )


def make_command(user: str = "user1", room: str = "room1") -> CommandEvent:
    return CommandEvent(
        message=make_message(user, room),
        prefix="!",
        invoked_with="test",
        parsed_args=None,
        parameters_text="",
    )


def use(cooldown: Cooldown, event) -> None:
    assert cooldown.get_retry_after(event) <= 0
    cooldown.update_rate_limit(event)


def make_twitch_message(channel_twitch) -> TwitchMessageEvent:
    return TwitchMessageEvent(
        data=mock.Mock(),
        text="!test",
        id="message",
        author_login="user1",
        author_name="user1",
        author_id="user1",
        author_roles={UserRole.USER},
        room_name="room1",
        room_id="room1",
        bot_user_login="bot",
        bot_user_name="bot",
        bot_user_id="bot",
        bot_twitch=mock.Mock(),
        channel_twitch=channel_twitch,
        twitch_chat=mock.AsyncMock(),
    )


class TwitchSending(unittest.IsolatedAsyncioTestCase):
    async def test_channel_without_its_own_client_uses_irc_quietly(self):
        event = make_twitch_message(None)
        with self.assertNoLogs("bot.commands.events", "WARNING"):
            await event.send("hello")
        event.twitch_chat.send_message.assert_awaited_once_with("room1", "hello", None)

    async def test_http_comes_first_when_the_channel_has_a_client(self):
        event = make_twitch_message(mock.AsyncMock())
        await event.send("hello")
        event.channel_twitch.send_chat_message.assert_awaited_once()
        event.twitch_chat.send_message.assert_not_awaited()

    async def test_failed_http_falls_back_to_irc(self):
        event = make_twitch_message(mock.AsyncMock())
        event.channel_twitch.send_chat_message.side_effect = RuntimeError
        with self.assertLogs("bot.commands.events", "WARNING"):
            await event.send("hello")
        event.twitch_chat.send_message.assert_awaited_once()


class CooldownBuckets(unittest.TestCase):
    def test_user_bucket_is_per_user(self):
        cooldown = Cooldown(1, 60, BucketType.USER)
        use(cooldown, make_command(user="user1"))
        self.assertGreater(cooldown.get_retry_after(make_command(user="user1", room="room2")), 0)
        self.assertEqual(cooldown.get_retry_after(make_command(user="user2")), 0)

    def test_channel_bucket_is_per_channel(self):
        cooldown = Cooldown(1, 60, BucketType.CHANNEL)
        use(cooldown, make_command(room="room1"))
        self.assertGreater(cooldown.get_retry_after(make_command(user="user2", room="room1")), 0)
        self.assertEqual(cooldown.get_retry_after(make_command(room="room2")), 0)

    def test_guild_bucket_is_per_channel(self):
        cooldown = Cooldown(1, 60, BucketType.GUILD)
        use(cooldown, make_command(room="room1"))
        self.assertEqual(cooldown.get_retry_after(make_command(room="room2")), 0)

    def test_global_bucket_is_shared(self):
        cooldown = Cooldown(1, 60, BucketType.GLOBAL)
        use(cooldown, make_command(user="user1", room="room1"))
        self.assertGreater(cooldown.get_retry_after(make_command(user="user2", room="room2")), 0)

    def test_several_buckets_must_all_match(self):
        cooldown = Cooldown(1, 60, [BucketType.USER, BucketType.CHANNEL])
        use(cooldown, make_command(user="user1", room="room1"))
        self.assertGreater(cooldown.get_retry_after(make_command(user="user1", room="room1")), 0)
        self.assertEqual(cooldown.get_retry_after(make_command(user="user2", room="room1")), 0)
        self.assertEqual(cooldown.get_retry_after(make_command(user="user1", room="room2")), 0)

    def test_message_and_command_events_share_a_bucket(self):
        cooldown = Cooldown(1, 60, BucketType.USER)
        use(cooldown, make_message())
        self.assertGreater(cooldown.get_retry_after(make_command()), 0)


class StackedChecks(unittest.TestCase):
    def test_stacked_checks_are_all_kept(self):
        first = HasRole(UserRole.MODERATOR)
        second = TwitchOnly()

        @checks(first)
        @checks(second)
        async def command(ctx):
            pass

        self.assertCountEqual(command._listener_command_checks, [first, second])


if __name__ == "__main__":
    unittest.main()
