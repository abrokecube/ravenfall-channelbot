import logging
from typing import (
    Any,
    override,
)

from bot.core.components import (
    BaseDispatcher,
    BaseEvent,
    BaseListener,
    Cooldown,
    GlobalContext,
)
from bot.core.enums import BucketType
from bot.core.exceptions import ListenerError, ListenerOnCooldown
from bot.integrations.chat_messages import EVENT_CATEGORY_MESSAGE
from bot.integrations.chat_messages.events import MessageEvent
from bot.integrations.chat_messages.exceptions import CheckFailure
from utils.format_time import TimeSize, format_seconds

from . import CommandArgs, CommandDispatchResult
from .events import CommandEvent
from .exceptions import (
    ArgumentConversionError,
    ArgumentError,
    CommandError,
    EmptyFlagValueError,
    MissingRequiredArgumentError,
    UnknownArgumentError,
    UnknownFlagError,
    VerificationFailure,
)
from .listeners import CommandListener

LOGGER = logging.getLogger(__name__)


class CommandDispatcher(BaseDispatcher):
    def __init__(self, case_sensitive: bool = False):
        super().__init__()
        self.identifier = CommandDispatcher
        self._func_listener: type[BaseListener] = CommandListener
        self.categories: set[str] = set([EVENT_CATEGORY_MESSAGE])
        self.listeners: dict[str, BaseListener] = {}
        self.listeners_and_aliases: dict[str, CommandListener] = {}
        self.error_cooldown: Cooldown = Cooldown(
            1, 5, [BucketType.USER, BucketType.CHANNEL]
        )
        self.case_sensitive: bool = case_sensitive

    @override
    def add_listener(self, listener: BaseListener):
        if listener.expected_dispatcher != self.identifier:
            msg = f"Listener {listener} cannot be assigned to this dispatcher!"
            raise ValueError(msg)

        if isinstance(listener, CommandListener):
            name: str = listener.name
            aliases: list[str] = listener.aliases.copy()
            cog_name = ""
            if listener.cog:
                cog_name: str = listener.cog.name
        else:
            msg = f"Listener {listener} cannot be assigned to this dispatcher!"
            raise ValueError(msg)

        if not self.case_sensitive:
            name = name.lower()
            aliases = [a.lower() for a in aliases]

        if name in self.listeners:
            other = self.listeners[name]
            msg = f"Command name '{name}' ({cog_name}) is taken by command '{other.id}' ({other.cog.__qualname__})"
            raise ValueError(msg)
        if name in self.listeners_and_aliases:
            other = self.listeners_and_aliases[name]
            msg = f"Command name '{name}' ({cog_name}) is taken by an alias of '{other.id}' ({other.cog.__qualname__})"
            raise ValueError(msg)
        for alias in aliases:
            if alias in self.listeners:
                other = self.listeners[alias]
                msg = f"Command alias '{alias}' of command '{name}' ({cog_name}) is taken by command '{other.id}' ({other.cog.__qualname__})"
                raise ValueError(msg)
            if alias in self.listeners_and_aliases:
                other = self.listeners_and_aliases[alias]
                msg = f"Command alias '{alias}' of command '{name}' ({cog_name}) is taken by an alias of '{other.id}' ({other.cog.__qualname__})"
                raise ValueError(msg)

        self.listeners[name] = listener
        self.listeners_and_aliases[name] = listener
        for alias in aliases:
            self.listeners_and_aliases[alias] = listener

    @override
    def remove_listener(self, listener: BaseListener):
        name: str = ""
        aliases: list[str] = []
        if isinstance(listener, CommandListener):
            name = listener.id
            aliases = listener.aliases.copy()
        else:
            name = listener.id
            aliases = []

        if not self.case_sensitive:
            name = name.lower()
            aliases = [a.lower() for a in aliases]

        if name not in self.listeners:
            msg = f"Dispatcher '{self.__qualname__}' does not have a listener with the name '{listener.id}'"
            raise ValueError(msg)

        __ = self.listeners.pop(name)
        __ = self.listeners_and_aliases.pop(name)
        for alias in aliases:
            __ = self.listeners_and_aliases.pop(alias)

    def _find_command(self, text: str) -> tuple[str, str]:
        norm_text = text
        if not self.case_sensitive:
            norm_text = text.lower()
        for cmd in sorted(self.listeners_and_aliases.keys(), key=len, reverse=True):
            if norm_text == cmd or norm_text.startswith(cmd + " "):
                return cmd, text[len(cmd) :].strip()
        return "", text

    @override
    async def dispatch(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        global_context: GlobalContext,
        event: BaseEvent,
        *args: Any,
        respond_to_errors: bool = True,
        no_prefix: bool = False,
        **kwargs: Any,
    ) -> CommandDispatchResult:
        if isinstance(event, MessageEvent):
            if no_prefix:
                prefix = ""
            else:
                prefix = await self.get_prefix(global_context, event)
            used_prefix = ""
            if isinstance(prefix, list):
                for p in prefix:
                    if event.text.startswith(p):
                        used_prefix = p
                        break
                else:
                    return CommandDispatchResult(None, None)
            else:
                if not event.text.startswith(prefix):
                    return CommandDispatchResult(None, None)
                used_prefix = prefix
            content = event.text[len(used_prefix) :]

            command_name, remaining_text = self._find_command(content)
            if not command_name or command_name not in self.listeners_and_aliases:
                return CommandDispatchResult(None, None)

            command = self.listeners_and_aliases[command_name]

            new_event = CommandEvent(
                message=event,
                prefix=used_prefix,
                invoked_with=content[: len(command_name)],
                parameters_text=remaining_text,
                parsed_args=CommandArgs(remaining_text),
            )
        elif isinstance(event, CommandEvent):
            new_event = event
            command_name, remaining_text = self._find_command(
                event.message.text[len(event.prefix) :]
            )
            command = self.listeners_and_aliases.get(command_name)
            if not command:
                return CommandDispatchResult(None, None)
        else:
            msg = f"Command dispatcher does not support event type '{event.__class__.__name__}'"
            raise ValueError(msg)

        try:
            await command.invoke(global_context, new_event)
            return CommandDispatchResult(command, None)
        except Exception as error:
            if not isinstance(error, ListenerError):
                LOGGER.exception("Error occurred during command invocation: %s", error)
            else:
                LOGGER.error("Error handled during command invocation: %s", error)
            if not respond_to_errors:
                raise error
            await self.on_invoke_error(global_context, new_event, error, command=command)
            return CommandDispatchResult(command, error)

    @override
    async def on_invoke_error(
        self,
        g_ctx: GlobalContext,
        event: BaseEvent,
        error: Exception,
        *args: Any,
        command: CommandListener | None = None,
        **kwargs: Any,
    ):
        if command is None:
            return
        if not isinstance(event, CommandEvent):
            return
        usage_text = command.get_usage_text(event.prefix, event.invoked_with)
        if isinstance(error, ListenerOnCooldown):
            if (
                error.cooldown.per >= 60
                and self.error_cooldown.get_retry_after(event) <= 0
            ):
                await event.message.reply(
                    f"❌ Listener '{command.name}' is on cooldown. Try again in {format_seconds(error.retry_after, TimeSize.LONG)}."
                )
                self.error_cooldown.update_rate_limit(event)
        elif isinstance(error, MissingRequiredArgumentError):
            await event.message.reply(
                f"❌ Usage: {usage_text} – Missing argument: {error.parameter.name}"
            )
        elif isinstance(error, EmptyFlagValueError):
            if not error.parameter:
                LOGGER.error("EmptyFlagValueError does not have an assigned parameter")
                await event.message.reply("❌ Expected a value for an argument")
            else:
                await event.message.reply(
                    f"❌ Expected a value for argument '{error.parameter.name}' (type: {error.parameter.type_title})"
                )
        elif isinstance(error, ArgumentConversionError):
            if not error.parameter:
                out_text = "❌ Error parsing an argument"
                LOGGER.error(
                    "ArgumentConversionError does not have an assigned parameter"
                )
            else:
                if error.parameter.name not in event.specified_parameters:
                    if error.message:
                        out_text = f"❌ Error parsing argument '{error.parameter.name}' (default value): {error.message}"
                    else:
                        out_text = f"❌ '{error.value}' ({error.parameter.name} default value) is not a valid {error.parameter.type_title}"
                else:
                    if error.message:
                        out_text = f"❌ Error parsing argument '{error.parameter.name}': {error.message}"
                    else:
                        out_text = f"❌ '{error.value}' ({error.parameter.name}) is not a valid {error.parameter.type_title}"
            await event.message.reply(out_text)
        elif isinstance(error, UnknownArgumentError):
            await event.message.reply(
                f"❌ Usage: {usage_text} – Unknown argument: {error.arguments[0]}"
            )
        elif isinstance(error, UnknownFlagError):
            await event.message.reply(
                f"❌ Usage: {usage_text} – Unknown parameter: {error.flag_name}"
            )
        elif isinstance(error, CheckFailure):
            if self.error_cooldown.get_retry_after(event) <= 0:
                await event.message.reply(f"❌ {error.message}")
                self.error_cooldown.update_rate_limit(event)
        elif isinstance(
            error, (VerificationFailure, ArgumentError, CommandError, ListenerError)
        ):
            await event.message.reply(f"❌ {error.message}")
        else:
            await event.message.reply("❌ An unknown error occurred")

    async def get_prefix(self, global_context: GlobalContext, event: MessageEvent) -> str:
        return "!"
