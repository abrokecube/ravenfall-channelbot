from bot.core.components import BaseCheck, GlobalContext, BaseEvent
from . import UserRole, MessageEvent
from typing import override

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
        if not isinstance(event, MessageEvent):
            raise ValueError("HasRole check can only be used with MessageEvent")
        if not any(role in event.author_roles for role in self.required_roles):
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
        if not isinstance(event, MessageEvent):
            raise ValueError("MinPermissionLevel check can only be used with MessageEvent")

        if any(r.level() >= self.min_level for r in event.author_roles):
            return True
        if any(r in self.extra_roles for r in event.author_roles):
            return True
        return f"You do not have permission to use this command."
        
