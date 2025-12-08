from __future__ import annotations
from typing import Optional, List, Dict
from ..cog import Cog
from ..commands import Context, Commands, Command, parameter
from utils.strutils import strjoin
from docstring_parser import parse
import inspect

class HelpCog(Cog):
    def __init__(self, commands: Commands, **kwargs):
        super().__init__(**kwargs)
        self.commands_manager = commands

    async def build_command_list_single_line(self, ctx: Context = None) -> str:
        cogs_dict: Dict[str, List[Command]] = {}
        
        if self.commands_manager.cog_manager:
            for cog_name, cog in self.commands_manager.cog_manager.loaded_cogs.items():
                cog_name: str
                cog: Cog
                cogs_dict[cog_name] = []
                for cmd_name, cmd in cog.commands.items():
                    cogs_dict[cog_name].append(cmd)
                
        commands_lists = []
        for cog_name, commands in cogs_dict.items():
            visible_cmds = []
            for c in commands:
                if c.hidden:
                    continue
                
                # Check if command should be hidden based on checks
                should_hide = False
                if ctx:
                    for check in c.checks:
                        if check.hide_in_help:
                            try:
                                check_result = check.check(ctx)
                                if inspect.isawaitable(check_result):
                                    check_result = await check_result
                                
                                if not check_result:
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
    
    def build_command_info_single_line(self, ctx: Context, command: Command, invoked_name: str) -> str:
        return f"Usage: {command.get_help_text(ctx.prefix, invoked_name)}"
    
    def build_arg_info_single_line(self, ctx: Context, command: Command, arg_name: str) -> str:
        matched_arg_name = command.arg_mappings.get(arg_name, None)
        if not matched_arg_name:
            return f"Argument '{arg_name}' not found in command '{command.name}'."
        param_data = command.parameters_map.get(matched_arg_name, {})
        return param_data.get_help_text(arg_name)

    @Cog.command(name="help")
    @parameter("command_name", greedy=True)
    async def help(self, ctx: Context, command_name: Optional[str] = None, **kwargs):
        """Shows help for a command or lists all commands.

        Args:
            command_name: The name of the command to show help for.
        """
        if kwargs:
            command_name += " " + " ".join([x for x in kwargs.keys()])
        if command_name:
            command, parameter = ctx.command.bot._find_command(command_name)
            if command is None:
                await ctx.reply(f"Command '{command_name}' not found.")
                return
            command = ctx.command.bot.dispatchers['command'].listeners[command]
            if not parameter:
                await ctx.reply(self.build_command_info_single_line(ctx, command, command_name))
            else:
                await ctx.reply(self.build_arg_info_single_line(ctx, command, parameter))
        else:
            await ctx.reply(await self.build_command_list_single_line(ctx))

def setup(commands: Commands, **kwargs) -> None:
    commands.load_cog(HelpCog, commands=commands, **kwargs)
