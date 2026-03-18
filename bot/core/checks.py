from __future__ import annotations

from typing import TYPE_CHECKING, override
if TYPE_CHECKING:
    from .events import CommandEvent
    from .global_context import GlobalContext
    from .types import CheckFuncType
from .events import BaseEvent
from .enums import UserRole, EventSource
from inspect import isawaitable

class BaseCheck:
    """
    Base class for all checks.
    
    Checks are used to validate whether a user is allowed to execute a command.
    They can return True to indicate success, or a string with an error message.
    
    To display a custom error message when conversion fails,
    raise command_exceptions.CheckError in the convert method.
    """
    title: str | None = None
    short_help: str | None = None
    help: str | None = None
    hide_in_help: bool = False

    async def check(self, g_ctx: GlobalContext, event: BaseEvent) -> bool | str:  # pyright: ignore[reportUnusedParameter]
        raise NotImplementedError

class FunctionCheck(BaseCheck):
    """Check using a function that returns bool or str (failure message)."""
    
    def __init__(self, predicate: CheckFuncType):
        self.predicate: CheckFuncType = predicate
        self.title: str | None = predicate.__name__.replace('_', ' ').title()
        self.help: str | None = getattr(predicate, '__doc__', '')
    
    @override
    async def check(self, g_ctx: GlobalContext, event: BaseEvent):
        result = self.predicate(event)
        if isawaitable(result):
            result = await result
        return result

class HasRole(BaseCheck):
    """Check if the user has at least one of the specified roles."""
    
    def __init__(self, *required_roles: UserRole):
        self.required_roles: tuple[UserRole, ...] = required_roles
        role_names = ', '.join(role.name.lower().replace("_", " ") for role in required_roles)
        self.title: str | None = role_names
        self.short_help: str | None = role_names
        self.hide_in_help: bool = True
        if len(required_roles) == 1:
            self.help: str | None = f"Requires the {role_names} role."
        else:
            self.help = f"Requires one of the following roles: {role_names}."
    
    @override
    async def check(self, g_ctx: GlobalContext, event: BaseEvent):
        if not isinstance(event, CommandEvent):
            raise ValueError("HasRole check can only be used with CommandEvent")
        if not any(role in event.message.author_roles for role in self.required_roles):
            return f"You do not have permission to use this command."
        return True

class MinPermissionLevel(BaseCheck):
    """Check if the user is at or above a permission level"""
    
    def __init__(self, minimum_role: UserRole, *, extra_roles: list[UserRole] | None = None):
        self.min_level: int = minimum_role.level()
        self.extra_roles: list[UserRole] = extra_roles or []
        self.title: str | None = minimum_role.name.lower().replace("_", " ")
        self.short_help: str | None = self.title
        self.hide_in_help: bool = True
        if not self.extra_roles:
            self.help: str | None = f"Must be {self.title}."
        else:
            role_names = ', '.join(role.name.lower().replace("_", " ") for role in self.extra_roles)
            self.help = f"Must be one of the following: {self.title}, {role_names}."
    
    @override
    async def check(self, g_ctx: GlobalContext, event: BaseEvent):
        if not isinstance(event, CommandEvent):
            raise ValueError("MinPermissionLevel check can only be used with CommandEvent")

        if any(r.level() >= self.min_level for r in event.message.author_roles):
            return True
        if any(r in self.extra_roles for r in event.message.author_roles):
            return True
        return f"You do not have permission to use this command."
        

class TwitchOnly(BaseCheck):
    title: str | None = "Twitch only"
    help: str | None = "Can only be run in Twitch"
    hide_in_help: bool = True
    
    @override
    async def check(self, g_ctx: GlobalContext, event: BaseEvent):
        if not isinstance(event, CommandEvent):
            raise ValueError("MinPermissionLevel check can only be used with CommandEvent")
        if event.message.platform != EventSource.Twitch:
            return "This command can only be run on Twitch."
        return True
