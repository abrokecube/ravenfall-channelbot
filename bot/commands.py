from __future__ import annotations

import asyncio
import logging
from typing import Callable, List, Dict, Awaitable, Union, Optional, TYPE_CHECKING
from twitchAPI.twitch import Twitch
from twitchAPI.chat import ChatMessage, Chat
from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionData
from dataclasses import dataclass
import re
from enum import Enum

# Configure logger for this module
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .cog import Cog, CogManager

# Type for command functions - can be either sync or async
CommandFunc = Callable[['CommandContext'], Union[None, Awaitable[None]]]
RedeemFunc = Callable[['RedeemContext'], Union[None, Awaitable[None]]]

class Commands:
    def __init__(self, chat: Chat, twitches: Dict[str, Twitch] = {}):
        self.chat: Chat = chat
        self.twitch: Twitch = chat.twitch
        self.twitches: Dict[str, Twitch] = twitches
        self.commands: Dict[str, Command] = {}
        self.redeems: Dict[str, Redeem] = {}
        self.prefix: str = "!"
        self.loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self.cog_manager: Optional[CogManager] = None

    def add_command(self, name: str, func: CommandFunc) -> Command:
        """Add a new command with the given name and handler function.
        
        Args:
            name: The command name (case-insensitive)
            func: The async or sync function to call when the command is used
                Should take a Context parameter as the first argument and
                optionally a cog instance as self if it's a cog command.
                
        Returns:
            The created Command instance
            
        Raises:
            ValueError: If a command with the same name already exists
        """
        cmd_name = name.lower()
        if cmd_name in self.commands:
            raise ValueError(f"Command '{cmd_name}' already exists")
            
        cmd = Command(cmd_name, func)
        self.commands[cmd.name] = cmd
        return cmd

    def add_redeem(self, name: str, func: RedeemFunc) -> RedeemFunc:
        redeem_name = name.lower()
        if redeem_name in self.redeems:
            raise ValueError(f"Redeem '{redeem_name}' already exists")
            
        redeem = Redeem(redeem_name, func)
        self.redeems[redeem.name] = redeem
        return redeem
        
    def setup_cog_manager(self) -> CogManager:
        """Set up and return the cog manager.
        
        Returns:
            The initialized CogManager instance.
        """
        if not hasattr(self, 'cog_manager') or self.cog_manager is None:
            from .cog import CogManager
            self.cog_manager = CogManager(self)
        return self.cog_manager
        
    def load_cog(self, cog_cls: type[Cog], **kwargs) -> None:
        """Load a cog.
        
        Args:
            cog_cls: The cog class to load.
            **kwargs: Additional arguments to pass to the cog's __init__
        """
        if self.cog_manager is None:
            self.setup_cog_manager()
        try:
            self.cog_manager.load_cog(cog_cls, **kwargs)
        except Exception as e:
            logger.error(f"Error loading cog '{cog_cls.name}':", exc_info=True)
            raise
        
    def unload_cog(self, cog_name: str) -> None:
        """Unload a cog.
        
        Args:
            cog_name: The name of the cog to unload.
        """
        if self.cog_manager:
            self.cog_manager.unload_cog(cog_name)
            
    def reload_cog(self, cog_cls: type[Cog], **kwargs) -> None:
        """Reload a cog.
        
        Args:
            cog_cls: The cog class to reload.
            **kwargs: Additional arguments to pass to the cog's __init__
        """
        if self.cog_manager:
            self.cog_manager.reload_cog(cog_cls, **kwargs)

    async def get_prefix(self, msg: ChatMessage) -> str:
        return self.prefix

    async def on_command_error(self, ctx: CommandContext, command: Command, error: Exception):
        ...
    
    async def on_redeem_error(self, ctx: RedeemContext, redeem: Redeem, error: Exception):
        ...
        
    def _find_command(self, text: str) -> tuple[str, str]:
        """Find the best matching command from the command text.
        Returns a tuple of (command_name, remaining_text)"""
        text_lower = text.lower()
        # Sort commands by length (longest first) to match the most specific command first
        for cmd in sorted(self.commands.keys(), key=len, reverse=True):
            if text_lower == cmd or text_lower.startswith(cmd + ' '):
                return cmd, text[len(cmd):].strip()
        return None, text

    def _find_redeem(self, name: str) -> tuple[str, str]:
        """Find the best matching redeem from the redeem text.
        Returns a tuple of (redeem_name, remaining_text)"""
        name_lower = name.lower()
        # Sort redeems by length (longest first) to match the most specific redeem first
        for redeem in sorted(self.redeems.keys(), key=len, reverse=True):
            if name_lower == redeem or name_lower.startswith(redeem + ' '):
                return redeem, name[len(redeem):].strip()
        return None, name

    async def process_message(self, msg: ChatMessage) -> None:
        """Process an incoming message and execute the corresponding command if found.
        
        Args:
            msg: The incoming chat message
        """
        prefix = await self.get_prefix(msg)
        if not msg.text.startswith(prefix):
            return
            
        # Remove prefix and clean up the content
        content = msg.text[len(prefix):]
        content = content.replace("\U000e0000", "").strip()
        
        # Find the best matching command
        command_name, remaining_text = self._find_command(content)
        if not command_name or command_name not in self.commands:
            return
            
        # Create context and execute the command
        ctx = CommandContext(msg, command_name, remaining_text)
        try:
            result = self.commands[command_name].func(ctx)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            await self.on_command_error(ctx, self.commands[command_name], e)
            logger.error(f"Error executing command '{command_name}':", exc_info=True)

    async def process_channel_point_redemption(self, redemption: ChannelPointsCustomRewardRedemptionData):
        redeem_name, remaining_text = self._find_redeem(redemption.reward.title)
        if not redeem_name or redeem_name not in self.redeems:
            return
            
        # Create context and execute the redeem
        ctx = RedeemContext(self.chat, self.twitches[redemption.broadcaster_user_id], redemption)
        try:
            result = self.redeems[redeem_name].func(ctx)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            await self.on_redeem_error(ctx, self.redeems[redeem_name], e)
            logger.error(f"Error executing redeem '{redeem_name}':", exc_info=True)

class Command:
    def __init__(self, name: str, func: CommandFunc, **kwargs):
        """Initialize a new command.
        
        Args:
            name: The command name (should be lowercase)
            func: The function to call when the command is used
            **kwargs: Additional command properties (e.g., aliases, help text)
        """
        self.name = name
        self.func = func
        self.aliases = kwargs.get('aliases', [])
        self.help = kwargs.get('help', '')
        self.hidden = kwargs.get('hidden', False)
        
    async def invoke(self, ctx: CommandContext) -> None:
        """Invoke the command with the given context."""
        try:
            # Get the function to call
            func = self.func
            
            # Call the function - it's already bound to the instance if it's a method
            result = func(ctx)
            
            # Await the result if it's a coroutine
            if asyncio.iscoroutine(result):
                await result
                
        except Exception as e:
            if hasattr(ctx, 'commands') and hasattr(ctx.commands, 'on_command_error'):
                await ctx.commands.on_command_error(ctx, self, e)
            else:
                # Log the error if we can't call the error handler
                logger.error("Error in command invocation:", exc_info=True)

class Redeem:
    def __init__(self, name: str, func: RedeemFunc, **kwargs):
        self.name = name
        self.func = func
        
    async def invoke(self, ctx: CommandContext, redemption: ChannelPointsCustomRewardRedemptionData) -> None:
        try:
            func = self.func
            result = func(ctx, redemption)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            if hasattr(ctx, 'commands') and hasattr(ctx.commands, 'on_redeem_error'):
                await ctx.commands.on_redeem_error(ctx, self, e)
            else:
                logger.error("Error in redeem invocation:", exc_info=True)

class CommandContext:
    def __init__(self, msg: ChatMessage, command: str, parameter: str):
        self.msg: ChatMessage = msg
        self.command: str = command
        self.parameter: str = parameter
        self.args: CommandArgs = CommandArgs(parameter)
        self.commands: Commands = Commands()

    async def reply(self, text: str):
        await self.msg.reply(text)

    async def send(self, text: str):
        await self.msg.chat.send_message(self.msg.room.name, text)

class CustomRewardRedemptionStatus(Enum):
    UNFULFILLED = 'UNFULFILLED'
    FULFILLED = 'FULFILLED'
    CANCELED = 'CANCELED'

class RedeemContext:
    def __init__(self, chat: Chat, twitch: Twitch, redemption: ChannelPointsCustomRewardRedemptionData):
        self.chat: Chat = chat
        self.twitch: Twitch = twitch
        self.redemption: ChannelPointsCustomRewardRedemptionData = redemption

    async def update_status(self, status: CustomRewardRedemptionStatus):
        if self.redemption.status == "unfulfilled":
            await self.twitch.update_redemption_status(
                self.redemption.broadcaster_user_id,
                self.redemption.reward.id,
                self.redemption.id,
                status
            )
        else:
            raise ValueError(f"Redemption is not in the UNFULFILLED state (current: {self.redemption.status})")

    async def send(self, text: str):
        await self.chat.send_message(self.redemption.broadcaster_user_login, text)
        

@dataclass
class Flag:
    name: str
    value: str = None

    def __repr__(self):
        return f"Flag({self.name}, {self.value})"

DELIMETERS = ('=', ':')
RE_FLAG = re.compile(r'[-a-zA-Z]{2}[a-zA-Z]+[:=]+.+|-[a-zA-Z]\b|--[a-zA-Z]+\b')

class CommandArgs:
    def __init__(self, text: str):
        self.text = text
        
        self.args: List[str | Flag] = []  # args are in order of appearance
        self.flags: List[Flag] = []  # flags are in order of appearance
        self._parse()

    def _parse(self):
        if not self.text.strip():
            return
            
        in_quotes = None  # None if not in quotes, otherwise the quote char (' or ")
        current = []
        args = []
        i = 0
        n = len(self.text)
        
        while i < n:
            char = self.text[i]
            
            # Handle quotes
            if char in ('"', "'"):
                if i > 0 and self.text[i-1] == '\\':
                    # Escaped quote, add to current and remove the backslash
                    current[-1] = char
                elif in_quotes is None:
                    # Start of quoted string
                    current.append('"')
                    in_quotes = char
                elif char == in_quotes:
                    # End of quoted string
                    current.append('"')
                    in_quotes = None
                else:
                    # Nested quotes of different type, add to current
                    current.append(char)
            elif char.isspace() and in_quotes is None:
                if current:
                    args.append(''.join(current))
                    current = []
            else:
                current.append(char)
                
            i += 1
                
        if current:
            args.append(''.join(current))
        
        for arg in args:
            delimiter_char = None
            has_delimiter = False
            for delimiter in DELIMETERS:
                if delimiter in arg:
                    has_delimiter = True
                    delimiter_char = delimiter
                    break
            is_quoted = arg[0] == '"' and arg[-1] == '"'
            if RE_FLAG.match(arg):
                flag_name: str = arg.lstrip('-')
                flag_value: str | None = None
                if has_delimiter:
                    if delimiter_char in flag_name:
                        flag_name, flag_value = flag_name.split(delimiter_char, 1)
                flag = Flag(flag_name, flag_value)
                self.flags.append(flag)
                self.args.append(flag)
            else:
                if is_quoted:
                    arg = arg[1:-1]
                self.args.append(arg)

    def get_flag(self, name: str | list[str], case_sensitive: bool = False) -> Flag | None:
        names = name if isinstance(name, list) else [name]
        for flag in self.flags:
            if case_sensitive and flag.name in names:
                return flag
            elif not case_sensitive and flag.name.lower() in [n.lower() for n in names]:
                return flag
        return None