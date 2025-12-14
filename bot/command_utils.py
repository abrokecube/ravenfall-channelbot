from typing import TYPE_CHECKING, List, Dict
if TYPE_CHECKING:
    from .commands import UserRole

from .commands import Check, TwitchContext, Context, Converter
from .command_exceptions import ArgumentConversionError
import re
    
class HasRole(Check):
    """Check if the user has at least one of the specified roles."""
    
    def __init__(self, *required_roles: 'UserRole'):
        self.required_roles = required_roles
        role_names = ', '.join(role.name.lower().replace("_", " ") for role in required_roles)
        self.title = role_names
        self.short_help = role_names
        self.hide_in_help = True
        if len(required_roles) == 1:
            self.help = f"Requires the {role_names} role."
        else:
            self.help = f"Requires one of the following roles: {role_names}."
    
    async def check(self, ctx) -> bool:
        if not any(role in ctx.roles for role in self.required_roles):
            return f"You do not have permission to use this command."
        return True

class TwitchOnly(Check):
    title = "Twitch only"
    help = "Can only be run in Twitch"
    
    async def check(self, ctx: Context):
        if not isinstance(ctx, TwitchContext):
            return "This command can only be run on Twitch."
        return True

class Choice(Converter):
    def __init__(self, definition: List[str] | Dict[str, List[str]], title=None, case_sensitive=False):
        super().__init__()
        string_map = {}
        choices = []
        if isinstance(definition, list):
            if case_sensitive:
                string_map = {x: x for x in definition}
            else:
                string_map = {x.lower(): x for x in definition}
            choices = definition
        elif isinstance(definition, dict):
            choices = list(definition.keys())
            if case_sensitive:
                string_map = {x: x for x in choices}
                for k, v in definition.items():
                    string_map.update({x: k for x in v})
            else:
                string_map = {x.lower(): x for x in choices}
                for k, v in definition.items():
                    string_map.update({x.lower(): k for x in v})
        else:
            raise TypeError()
        
        if title:
            self.title = title
        else:
            self.title = f"{len(choices)} options"
        self.short_help = ", ".join(choices)
        self.help = f"Choices: {self.short_help}"
        self.case_sensitive = case_sensitive
        self.string_map = string_map
        
    def convert(self, ctx: Context, arg: str) -> str:
        if not arg in self.string_map:
            raise ArgumentConversionError(f"Choice '{arg}' is not a valid option. Valid choices: {self.short_help}")
        return self.string_map[arg]
        
class Regex(Converter):
    title = "Regex"
    short_help = "A python regular expression"
    help = "A python regular expression"
    
    @classmethod
    async def convert(cls, ctx: Context, arg: str) -> re.Pattern:
        try:
            return re.compile(arg)
        except Exception as e:
            raise ArgumentConversionError("Couldn't compile regex expression")
    

