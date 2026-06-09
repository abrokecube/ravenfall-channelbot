from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from bot.core.components import Cog, EventManager
from bot.integrations.chat_messages.utils import min_permission_level
from bot.integrations.commands import (
    CommandError,
    CommandEvent,
    command,
)
from bot.integrations.ravenfall.command_registry import ALIASES, COMMANDS, CommandDef
from bot.services.ravenfall_channels import (
    RavenfallChannelService,
    RavenfallLinkedChannel,
)

if TYPE_CHECKING:
    from bot.clients.ravenfall_middleman import Sender
    from bot.integrations.chat_messages import MessageEvent
    from bot.integrations.ravenfall import RavenfallInstance

LOGGER = logging.getLogger(__name__)

_DEFAULT_PREFIX = ">"


async def _build_and_send(
    instance: RavenfallInstance,
    sender: Sender,
    command_def: CommandDef,
    args: str,
) -> None:
    payload = command_def.build(args)
    __ = await instance.send_to_ravenfall(sender, payload)


def _get_channel_config(
    channel_srv: RavenfallChannelService,
    instance: RavenfallInstance,
    channel_id: str,
) -> RavenfallLinkedChannel | None:
    channels = channel_srv.get_channels(instance.channel_name)
    for ch in channels:
        if ch.id == channel_id:
            return ch
    return None


class RavenfallCommandsCog(Cog):
    """Handles Ravenfall game commands from linked channels."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self._channel_srv: RavenfallChannelService | None = None

    @override
    async def setup(self) -> None:
        channel_srv: RavenfallChannelService = self.global_context.require_service(
            RavenfallChannelService
        )
        self._channel_srv = channel_srv
        channel_srv.register_message_event_callback(self._on_ravenfall_command)

    @override
    async def teardown(self) -> None:
        if self._channel_srv:
            self._channel_srv.unregister_message_event_callback(
                self._on_ravenfall_command
            )

    async def _on_ravenfall_command(
        self, event: MessageEvent, instance: RavenfallInstance
    ) -> None:
        if not self._channel_srv:
            return

        channel_config = _get_channel_config(self._channel_srv, instance, event.room_id)
        if not channel_config:
            return
        if channel_config.uses_ravenbot:
            return
        if not channel_config.enable_ravenfall_commands:
            return

        prefix = channel_config.ravenfall_command_prefix or _DEFAULT_PREFIX
        if not event.text.startswith(prefix):
            return

        remaining = event.text[len(prefix) :].strip()
        if not remaining:
            return

        cmd_name, _, args_str = remaining.partition(" ")
        args_str = args_str.strip()

        command_def = self._find_command(cmd_name)
        if not command_def:
            return

        if not self._check_permission(command_def, event):
            return

        try:
            sender = await self._channel_srv.get_sender_from_message_event_user(event)
        except ValueError:
            LOGGER.warning(
                "Could not create sender for %s in %s",
                event.author_login,
                event.room_name,
            )
            return

        await _build_and_send(instance, sender, command_def, args_str)

    def _find_command(self, cmd_name: str) -> CommandDef | None:
        cmd_name = cmd_name.lower()
        alias_target = ALIASES.get(cmd_name)
        if alias_target:
            cmd_name = alias_target

        # Exact match first
        if cmd_name in COMMANDS:
            return COMMANDS[cmd_name]

        # Try longest prefix match for subcommands
        matched: tuple[str, CommandDef] | None = None
        for key, defn in COMMANDS.items():
            if (cmd_name.startswith(key + " ") or cmd_name == key) and (
                matched is None or len(key) > len(matched[0])
            ):
                matched = (key, defn)

        if matched is not None:
            return matched[1]

        return None

    def _check_permission(self, command_def: CommandDef, event: MessageEvent) -> bool:
        required = command_def.min_permission
        if required == "everyone":
            return True
        from bot.integrations.chat_messages import UserRole

        role_map = {
            "moderator": UserRole.MODERATOR,
            "broadcaster": UserRole.ADMINISTRATOR,
        }
        min_role = role_map.get(required)
        if min_role is None:
            return True
        return min_permission_level(event, min_role)

    @command("rfhelp")
    async def rfhelp(self, ctx: CommandEvent, *command_path: str) -> None:
        """Show help for Ravenfall commands."""
        if not command_path:
            await ctx.reply(self._format_help_list())
            return

        lookup = " ".join(command_path).lower()
        if lookup in ALIASES:
            lookup = ALIASES[lookup]

        command_def = COMMANDS.get(lookup)
        if not command_def:
            msg = f"Unknown command: {lookup}"
            raise CommandError(msg)

        await ctx.reply(self._format_help_command(lookup, command_def))

    def _format_help_list(self) -> str:
        names = sorted(COMMANDS.keys())
        parts: list[str] = []
        for name in names:
            cmd = COMMANDS[name]
            if cmd.min_permission != "everyone":
                continue
            parts.append(name)
        joined = ", ".join(parts)
        return (
            f"Ravenfall commands: {joined} "
            f"Use {_DEFAULT_PREFIX}rfhelp <command> for details."
        )

    def _format_help_command(self, name: str, cmd: CommandDef) -> str:
        lines: list[str] = []
        lines.append(f"» {name} — {cmd.help_text}")

        alias_list = cmd.aliases
        if alias_list:
            lines.append(f"  Aliases: {', '.join(alias_list)}")

        if cmd.min_permission != "everyone":
            lines.append(f"  Permissions: {cmd.min_permission}")
        else:
            lines.append("  Permissions: everyone")

        sub_names = [
            k for k, _ in COMMANDS.items() if k.startswith(name + " ") and k != name
        ]
        if sub_names:
            short = sorted([s[len(name) + 1 :] for s in sub_names])
            lines.append(f"  Subcommands: {', '.join(short)}")

        return "\n".join(lines)
