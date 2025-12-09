import sys
from unittest.mock import MagicMock, AsyncMock

# Mock twitchAPI
sys.modules["twitchAPI"] = MagicMock()
sys.modules["twitchAPI.twitch"] = MagicMock()
sys.modules["twitchAPI.chat"] = MagicMock()
sys.modules["twitchAPI.object"] = MagicMock()
sys.modules["twitchAPI.object.eventsub"] = MagicMock()

import asyncio
from bot.commands import Commands, Command
from bot.command_contexts import TwitchContext
from bot.command_enums import Platform

# Mock ChatMessage
class MockUser:
    def __init__(self, name):
        self.name = name
        self.mod = False
        self.subscriber = False

class MockRoom:
    def __init__(self, name):
        self.name = name
        self.room_id = "123"

class MockChatMessage:
    def __init__(self, text, user, room):
        self.text = text
        self.user = user
        self.room = room
        self.id = "msg_id"
        self.reply = AsyncMock()
        self.chat = MagicMock()
        self.chat.send_message = AsyncMock()

async def test_context_properties():
    # Setup
    chat = MagicMock()
    chat.twitch = MagicMock()
    commands = Commands(chat)
    
    # Register a command
    async def test_cmd(ctx):
        pass
    
    commands.add_command("test", test_cmd)
    
    # Test case 1: Normal invocation
    msg = MockChatMessage("!test args", MockUser("user"), MockRoom("channel"))
    
    # We need to manually trigger process_message logic or mock it enough to check context creation.
    # Since process_message creates the context and sets properties, we should call it.
    
    # Mock get_prefix to return "!"
    commands.get_prefix = AsyncMock(return_value="!")
    
    # Mock invoke to capture context
    captured_ctx = None
    async def mock_invoke(ctx):
        nonlocal captured_ctx
        captured_ctx = ctx
    
    commands.commands["test"].invoke = mock_invoke
    
    await commands.process_message(Platform.TWITCH, msg)
    
    assert captured_ctx is not None
    print(f"Full message: {captured_ctx.full_message}")
    print(f"Prefix: {captured_ctx.prefix}")
    print(f"Invoked with: {captured_ctx.invoked_with}")
    print(f"Command: {captured_ctx.command.name}")
    
    assert captured_ctx.full_message == "!test args"
    assert captured_ctx.prefix == "!"
    assert captured_ctx.invoked_with == "test"
    assert captured_ctx.command.name == "test"
    
    # Test case 2: Mixed case invocation
    msg2 = MockChatMessage("!TeSt args", MockUser("user"), MockRoom("channel"))
    await commands.process_message(Platform.TWITCH, msg2)
    
    assert captured_ctx.invoked_with == "TeSt"
    assert captured_ctx.command.name == "test"
    
    print("✅ All tests passed!")

if __name__ == "__main__":
    asyncio.run(test_context_properties())
