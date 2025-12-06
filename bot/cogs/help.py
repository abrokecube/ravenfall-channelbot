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

    def build_command_list_single_line(self) -> str:
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
            visible_cmds = [c.name for c in commands if not c.hidden]
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
    async def help(self, ctx: Context, command_name: Optional[str] = None):
        """Shows help for a command or lists all commands.

        Args:
            command_name: The name of the command to show help for.
        """
        if command_name:
            command, parameter = ctx.command.bot._find_command(command_name)
            if command is None:
                await ctx.reply(f"Command '{command_name}' not found.")
                return
            if not parameter:
                await ctx.reply(self.build_command_info_single_line(ctx, ctx.command.bot.commands[command], command_name))
            else:
                await ctx.reply(self.build_arg_info_single_line(ctx, ctx.command.bot.commands[command], parameter))
        else:
            await ctx.reply(self.build_command_list_single_line())

def setup(commands: Commands, **kwargs) -> None:
    commands.load_cog(HelpCog, commands=commands, **kwargs)
