from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple, Any, List, Callable
if TYPE_CHECKING:
    from events import CommandEvent
    from .global_context import GlobalContext
from .events import BaseEvent
from .enums import UserRole, EventSource

class BaseCheck:
    """To display a custom error message when conversion fails,
    raise command_exceptions.CheckError in the convert method."""
    title: str = None
    short_help: str = None
    help: str = None
    hide_in_help: bool = False

    async def check(self, g_ctx: GlobalContext, event: CommandEvent) -> bool:
        raise NotImplementedError


CheckFunc = Callable[[BaseEvent], bool]
class FunctionCheck(BaseCheck):
    def __init__(self, predicate: CheckFunc):
        self.predicate = predicate
        self.title = predicate.__name__.replace('_', ' ').title()
        self.help = getattr(predicate, '__doc__', '')
    
    def check(self, ctx: BaseEvent) -> bool:
        return self.predicate(ctx)


class HasRole(BaseCheck):
    """Check if the user has at least one of the specified roles."""
    
    def __init__(self, *required_roles: UserRole):
        self.required_roles = required_roles
        role_names = ', '.join(role.name.lower().replace("_", " ") for role in required_roles)
        self.title = role_names
        self.short_help = role_names
        self.hide_in_help = True
        if len(required_roles) == 1:
            self.help = f"Requires the {role_names} role."
        else:
            self.help = f"Requires one of the following roles: {role_names}."
    
    async def check(self, g_ctx, event) -> bool:
        if not any(role in event.message.author_roles for role in self.required_roles):
            return f"You do not have permission to use this command."
        return True

class TwitchOnly(BaseCheck):
    title = "Twitch only"
    help = "Can only be run in Twitch"
    hide_in_help = True
    
    async def check(self, g_ctx, event):
        if event.message.platform != EventSource.Twitch:
            return "This command can only be run on Twitch."
        return True
