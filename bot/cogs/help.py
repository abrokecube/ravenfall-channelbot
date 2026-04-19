from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from bot.core.components import Cog
from bot.integrations.chat_messages.exceptions import CheckFailure
from bot.integrations.commands.deco import command, parameter
from bot.integrations.commands.events import CommandEvent
from bot.integrations.commands.listeners import CommandListener
from bot.integrations.commands.services import CommandService

if TYPE_CHECKING:
    from bot.core.components import EventManager
    from bot.integrations.commands.dispatchers import CommandDispatcher

LOGGER = logging.getLogger(__name__)


class HelpCog(Cog):
    """Help command cog."""

    def __init__(self, event_manager: EventManager):
        super().__init__(event_manager)

    async def build_command_list_single_line(
        self, ctx: CommandEvent | None = None, *, show_more: bool = False
    ) -> str:
        """Builds a single line string listing all commands.

        Optionally includes commands the user doesn't have access to.
        """
        cogs_dict: dict[str, list[CommandListener]] = defaultdict(list)
        cmd_dispatcher: CommandDispatcher = self.global_context.require_service(
            CommandService
        ).dispatcher

        for lis in cmd_dispatcher.listeners.values():
            cog_name = None
            if lis.cog:
                cog_name = lis.cog.name
            else:
                continue
            if isinstance(lis, CommandListener):
                cogs_dict[cog_name].append(lis)

        commands_lists: list[str] = []
        for commands in cogs_dict.values():
            visible_cmds: list[str] = []
            for c in commands:
                if c.hidden:
                    continue

                # Check if command should be hidden based on checks
                should_hide = False
                if ctx and not show_more:
                    for check in c.checks:
                        if check.will_hide_command_from_help:
                            try:
                                check_result = check.check(self.global_context, ctx)
                                if inspect.isawaitable(check_result):
                                    check_result = await check_result

                                if isinstance(check_result, str) or not check_result:
                                    should_hide = True
                                    break
                            except CheckFailure:
                                should_hide = True
                                break
                            except Exception as e:  # noqa: BLE001
                                LOGGER.warning(
                                    f"Check failed to execute: {e}", exc_info=True
                                )

                if not should_hide:
                    visible_cmds.append(c.name)

            if visible_cmds:
                commands_lists.append(", ".join(sorted(visible_cmds)))

        return f"Commands: {' | '.join(commands_lists)}"

    def build_command_info_single_line(
        self, ctx: CommandEvent, command: CommandListener, invoked_name: str
    ) -> str:
        """Single line help for a command."""
        return f"Usage: {command.get_help_text(ctx.prefix, invoked_name)}"

    def build_arg_info_single_line(
        self, ctx: CommandEvent, command: CommandListener, arg_name: str
    ) -> str:
        """Single line help for a command argument."""
        matched_arg_name = command.arg_mappings.get(arg_name, None)
        if not matched_arg_name:
            return f"Argument '{arg_name}' not found in command '{command.name}'."
        param_data = command.parameters_map.get(matched_arg_name)
        if not param_data:
            return "No data"
        return param_data.get_help_text(arg_name)

    @command(name="help")
    @parameter("command_name", greedy=True)
    @parameter("all_", display_name="all", aliases=["a", "more", "m"])
    async def help(
        self,
        ctx: CommandEvent,
        command_name: str = "",
        *,
        all_: bool = False,
        **kwargs: str,
    ):
        """Get help for a command or lists all commands.

        Args:
            command_name: The name of the command to show help for.
            all_: Lists commands you don't have permission to use.

        """
        cmd_dispatcher: CommandDispatcher = self.global_context.require_service(
            CommandService
        ).dispatcher

        # if kwargs:
        #     command_name += " " + " ".join([x for x in kwargs])
        if command_name:
            command, parameter = cmd_dispatcher._find_command(command_name)
            if not command:
                await ctx.message.reply(f"Command '{command_name}' not found.")
                return
            command = cmd_dispatcher.listeners_and_aliases[command]
            if not parameter:
                await ctx.message.reply(
                    self.build_command_info_single_line(ctx, command, command_name)
                )
            else:
                await ctx.message.reply(
                    self.build_arg_info_single_line(ctx, command, parameter)
                )
        else:
            await ctx.message.reply(
                await self.build_command_list_single_line(ctx, show_more=all_)
            )
