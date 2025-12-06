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
        command_func = command.func
        doc_string = command_func.__doc__ or ""
        nm_out = [f"Usage: {ctx.prefix}{invoked_name}"]
        description = ""
        doc_parsed = None

        if doc_string:
            doc_parsed = parse(doc_string)
            description = doc_parsed.description

        if doc_parsed and doc_parsed.params:
            for param in doc_parsed.params:
                param_str = param.arg_name
                param_options = command.parameters_map.get(command.arg_mappings.get(param_str, ""), {})
                if param_options.hidden:
                    continue
                if param.type_name:
                    param_str += f": {param.type_name}"
                if param.is_optional:
                    param_str = f"({param_str})"
                else:
                    param_str = f"<{param_str}>"
                nm_out.append(param_str)
        else:
            func_inspect = inspect.signature(command_func)
            for param in [x for x in func_inspect.parameters.values()][2:]:
                param_str = param.name
                param_options = command.parameters_map.get(command.arg_mappings.get(param_str, ""), {})
                if param_options.hidden:
                    continue
                param_type = param.annotation
                param_type_name = ""
                param_is_optional = param.default != param.empty
                if param_type in (str, int, float):
                    param_type_name = param_type.__name__
                if param_type_name:
                    param_str += f": {param_type_name}"
                if param_is_optional:
                    param_str = f"({param_str})"
                else:
                    param_str = f"<{param_str}>"
                nm_out.append(param_str)
        
        name_and_usage = " ".join(nm_out)
        aliases = ""
        if command.aliases:
            alias_list = list(command.aliases)
            if invoked_name != command.name:
                alias_list.remove(invoked_name)
                alias_list.append(command.name)
            alias_list.sort()
            aliases = f"Aliases: {', '.join(alias_list)}"
        restrictions = ""
        if command.checks:
            restr_to = []
            for check in command.checks:
                if check.__doc__:
                    restr_to.append(check.__doc__)
                else:
                    restr_to.append(check.__name__)
            restrictions = f"Limited to: {', '.join(restr_to)}"

        response = strjoin(' – ', name_and_usage, description, restrictions, aliases)
        return response
    
    def build_arg_info_single_line(self, ctx: Context, command: Command, arg_name: str) -> str:
        command_func = command.func
        doc_string = command_func.__doc__ or ""
        doc_parsed = None

        if doc_string:
            doc_parsed = parse(doc_string)

        matched_arg_name = command.arg_mappings.get(arg_name, arg_name)
        param_found = False
        param_is_optional = False
        param_aliases: List[str] = []
        param_description = ""
        param_type_name = ""
        if doc_parsed and doc_parsed.params:
            for param in doc_parsed.params:
                if param.arg_name == matched_arg_name:
                    param_is_optional = param.is_optional
                    param_type_name = param.type_name or ""
                    param_description = param.description
                    param_found = True
                    break
        else:
            func_inspect = inspect.signature(command_func)
            for param in [x for x in func_inspect.parameters.values()][2:]:
                if param.name == matched_arg_name:
                    param_is_optional = param.default != param.empty
                    param_type = param.annotation
                    if param_type in (str, int, float):
                        param_type_name = param_type.__name__
                    param_found = True
                    break
        if not param_found:
            return f"Argument '{arg_name}' not found in command '{command.name}'."
        param_options = command.parameters_map.get(command.arg_mappings.get(arg_name, ""), {})
        param_is_hidden = param_options.hidden
        param_aliases = param_options.aliases[:]
        
        if arg_name in param_aliases:
            param_aliases.remove(arg_name)
            param_aliases.append(matched_arg_name)
        param_aliases.sort()

        out_str = []
        param_str = arg_name
        if param_type_name:
            param_str += f": {param_type_name}"
        if param_is_optional:
            param_str = f"({param_str})"
        else:
            param_str = f"<{param_str}>"
        out_str.append(param_str)
        out_str.append(param_description)
        if param_is_optional:
            out_str.append("Optional")
        else:
            out_str.append("Required")
        if param_aliases:
            out_str.append(f"Aliases: {', '.join(param_aliases)}")
            
        response = strjoin(' – ', *out_str)
        return response

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
