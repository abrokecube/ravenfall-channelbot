from __future__ import annotations
from typing import Optional, List, Dict
from ..commands.cog import Cog
from ..commands.events import CommandEvent
from ..commands.listeners import CommandListener
from ..commands.enums import (
    Dispatcher
)
from ..commands.dispatchers import CommandDispatcher
from ..commands.decorators import (
    command, parameter
)
from utils.strutils import strjoin
from docstring_parser import parse
import inspect
from collections import defaultdict

class HelpCog(Cog):
    async def build_command_list_single_line(self, ctx: CommandEvent = None, show_more=False) -> str:
        cogs_dict: Dict[str, List[CommandListener]] = defaultdict(list)
        cmd_dispatcher: CommandDispatcher = self.event_manager.dispatchers.get(Dispatcher.Command)
        
        if cmd_dispatcher is not None:
            for lis in cmd_dispatcher.listeners.values():
                cog_name = None
                if lis.cog:
                    cog_name = lis.cog.__class__.__name__
                cogs_dict[cog_name].append(lis)
                
        commands_lists = []
        for cog_name, commands in cogs_dict.items():
            visible_cmds = []
            for c in commands:
                if c.hidden:
                    continue
                
                # Check if command should be hidden based on checks
                should_hide = False
                if ctx and not show_more:
                    for check in c.checks:
                        if check.hide_in_help:
                            try:
                                check_result = check.check(ctx)
                                if inspect.isawaitable(check_result):
                                    check_result = await check_result
                                
                                if isinstance(check_result, str) or not check_result:
                                    should_hide = True
                                    break
                            except:
                                should_hide = True
                                break
                
                if not should_hide:
                    visible_cmds.append(c.name)
            
            if visible_cmds:
                commands_lists.append(', '.join(sorted(visible_cmds)))
                
        return f"Commands: {' | '.join(commands_lists)}"
    
    def build_command_info_single_line(self, ctx: CommandEvent, command: CommandListener, invoked_name: str) -> str:
        return f"Usage: {command.get_help_text(ctx.prefix, invoked_name)}"
    
    def build_arg_info_single_line(self, ctx: CommandEvent, command: CommandListener, arg_name: str) -> str:
        matched_arg_name = command.arg_mappings.get(arg_name, None)
        if not matched_arg_name:
            return f"Argument '{arg_name}' not found in command '{command.name}'."
        param_data = command.parameters_map.get(matched_arg_name, {})
        return param_data.get_help_text(arg_name)

    @command(name="help")
    @parameter("command_name", greedy=True)
    @parameter("all_", display_name="all", aliases=['a','more', 'm'])
    async def help(self, ctx: CommandEvent, command_name: Optional[str] = None, all_: bool = False, **kwargs):
        """Shows help for a command or lists all commands.

        Args:
            command_name: The name of the command to show help for.
            all_: Lists commands you don't have permission to use.
        """
        cmd_dispatcher: CommandDispatcher = self.event_manager.dispatchers.get(Dispatcher.Command)
        if not cmd_dispatcher:
            return
        
        if kwargs:
            command_name += " " + " ".join([x for x in kwargs.keys()])
        if command_name:
            command, parameter = cmd_dispatcher._find_command(command_name)
            if command is None:
                await ctx.message.reply(f"Command '{command_name}' not found.")
                return
            command = cmd_dispatcher.listeners_and_aliases[command]
            if not parameter:
                await ctx.message.reply(self.build_command_info_single_line(ctx, command, command_name))
            else:
                await ctx.message.reply(self.build_arg_info_single_line(ctx, command, parameter))
        else:
            await ctx.message.reply(await self.build_command_list_single_line(ctx, show_more=all_))

