from __future__ import annotations
from typing import Optional, List, Dict
from ..cog import Cog
from ..commands import Context, Commands, Command

class HelpCog(Cog):
    def __init__(self, commands: Commands, **kwargs):
        super().__init__(**kwargs)
        self.commands_manager = commands

    @Cog.command(name="help")
    async def help(self, ctx: Context, command_name: Optional[str] = None):
        """Shows help for a command or lists all commands.

        Args:
            command_name: The name of the command to show help for.
        """
        if command_name:
            cmd = self.commands_manager.commands.get(command_name.lower())
            if not cmd:
                await ctx.reply(f"Command '{command_name}' not found.")
                return

            # Build detailed help
            lines = []
            lines.append(f"Command: !{cmd.name}")
            if cmd.aliases:
                lines.append(f"Aliases: {', '.join(cmd.aliases)}")
            
            if cmd.description:
                lines.append(f"\n{cmd.description}")
            elif cmd.help:
                lines.append(f"\n{cmd.help}")

            if cmd.doc_args:
                lines.append("\nArguments:")
                for arg_name, arg_info in cmd.doc_args.items():
                    lines.append(f"  {arg_name} ({arg_info['type']}): {arg_info['description']}")

            if cmd.examples:
                lines.append("\nExamples:")
                for example in cmd.examples:
                    lines.append(f"  {example}")

            await ctx.reply("\n".join(lines))
        else:
            # List all commands grouped by Cog
            # We need to access cogs. The Commands object has a cog_manager.
            
            cogs_dict: Dict[str, List[Command]] = {}
            uncategorized: List[Command] = []

            # Map commands to cogs
            # This is a bit inefficient but works. 
            # Ideally Command should know its Cog, or we iterate Cogs.
            
            known_commands = set()
            
            if self.commands_manager.cog_manager:
                for cog_name, cog in self.commands_manager.cog_manager.loaded_cogs.items():
                    cogs_dict[cog_name] = []
                    for cmd_name, cmd in cog.commands.items():
                        cogs_dict[cog_name].append(cmd)
                        known_commands.add(cmd.name)
            
            # Find uncategorized commands
            for name, cmd in self.commands_manager.commands.items():
                if name not in known_commands and not cmd.hidden:
                    uncategorized.append(cmd)

            lines = ["Available Commands:"]
            
            for cog_name, commands in cogs_dict.items():
                visible_cmds = [c.name for c in commands if not c.hidden]
                if visible_cmds:
                    lines.append(f"\n{cog_name.capitalize()}: {', '.join(sorted(visible_cmds))}")
            
            if uncategorized:
                visible_cmds = [c.name for c in uncategorized]
                if visible_cmds:
                    lines.append(f"\nOther: {', '.join(sorted(visible_cmds))}")

            await ctx.reply("\n".join(lines))

def setup(commands: Commands, **kwargs) -> None:
    commands.load_cog(HelpCog, commands=commands, **kwargs)
