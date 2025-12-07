from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .commands import UserRole

from .commands import Check
    
class HasRole(Check):
    """Check if the user has at least one of the specified roles."""
    
    def __init__(self, *required_roles: 'UserRole'):
        self.required_roles = required_roles
        role_names = ', '.join(role.name.lower().replace("_", " ") for role in required_roles)
        self.title = role_names
        self.short_help = role_names
        if len(required_roles) == 1:
            self.help = f"Requires the {role_names} role."
        else:
            self.help = f"Requires one of the following roles: {role_names}."
    
    async def check(self, ctx) -> bool:
        if not any(role in ctx.roles for role in self.required_roles):
            return f"You do not have permission to use this command."
        return True
