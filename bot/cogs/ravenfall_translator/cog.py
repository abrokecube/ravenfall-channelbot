from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from bot.core.components import Cog
from bot.core.decorators import on_match, priority
from bot.integrations.ravenfall import RavenfallMessageEvent
from bot.integrations.ravenfall.events import MessageOrigin
from bot.integrations.ravenfall.models import RavenfallFormattedMessage
from bot.integrations.ravenfall.translator import TemplateTranslator

if TYPE_CHECKING:
    from bot.core.components import EventManager, GlobalContext
    from bot.integrations.ravenfall import RavenfallInstance
    from bot.services.ravenfall_channels import RavenfallChannelService

LOGGER = logging.getLogger(__name__)


class RavenfallTranslatorCog(Cog):
    """Translates Ravenfall messages using per-instance/per-channel translation files.

    Runs after the matcher has identified the message and extracted format_args.
    Resolves {{eval}} expressions in the translation template, sets the result
    on event.message.format, and optionally blocks suppressed messages.
    """

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self._channel_srv: RavenfallChannelService | None = None
        self._translators: dict[str, TemplateTranslator] = {}

    @override
    async def setup(self) -> None:
        from bot.services.ravenfall_channels import RavenfallChannelService

        self._channel_srv = await self.global_context.wait_for_service(
            RavenfallChannelService
        )

    def _get_translator(self, instance: RavenfallInstance) -> TemplateTranslator | None:
        path = instance.config.translations_path

        if not path:
            return None

        if path not in self._translators:
            t = TemplateTranslator()
            t.load(path)
            self._translators[path] = t
        return self._translators[path]

    @priority(20)
    @on_match(RavenfallMessageEvent)
    async def _translate(self, event: RavenfallMessageEvent, _match: object) -> None:
        msg = event.message
        if not isinstance(msg, RavenfallFormattedMessage):
            return
        if not msg.identifier:
            return

        translator = self._get_translator(event.ravenfall)
        if not translator:
            return

        result = translator.translate(msg.identifier, msg.format, msg.format_args)
        if result is None:
            return

        msg.format = result

        if not result.strip() and event.message_source == MessageOrigin.PROCESSOR:
            event.block()
