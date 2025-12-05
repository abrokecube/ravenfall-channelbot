class CommandError(Exception):
    """Base exception for command-related errors."""
    def __init__(self, message: str = "Command error"):
        self.message = message
        super().__init__(self.message)

class CheckFailure(CommandError):
    """Raised when a command check fails."""
    def __init__(self, message: str = "Check failed"):
        super().__init__(message)

class CommandRegistrationError(CommandError):
    """Raised when there's an error registering a command or redeem."""
    def __init__(self, name: str, item_type: str = "Command"):
        self.name = name
        self.item_type = item_type
        super().__init__(f"{item_type} '{name}' already exists")

class ArgumentError(CommandError):
    """Base exception for argument parsing errors."""
    pass

class UnknownFlagError(ArgumentError):
    """Raised when an unknown flag is provided."""
    def __init__(self, flag_name: str):
        self.flag_name = flag_name
        super().__init__(f"Unknown flag '{flag_name}'")

class DuplicateParameterError(ArgumentError):
    """Raised when a parameter is provided multiple times."""
    def __init__(self, param_name: str):
        self.param_name = param_name
        super().__init__(f"Multiple values provided for parameter '{param_name}'")

class MissingRequiredArgumentError(ArgumentError):
    """Raised when a required argument is missing."""
    def __init__(self, param_name: str, keyword_only: bool = False):
        self.param_name = param_name
        self.keyword_only = keyword_only
        arg_type = "keyword-only argument" if keyword_only else "argument"
        super().__init__(f"Missing required {arg_type}: {param_name}")

class UnknownArgumentError(ArgumentError):
    """Raised when unknown arguments are provided."""
    def __init__(self, args: list):
        self.args = args
        args_str = ', '.join(f"'{arg}'" for arg in args) if isinstance(args[0], str) else ' '.join(str(a) for a in args)
        super().__init__(f"Unknown arguments: {args_str}")

class ArgumentConversionError(ArgumentError):
    """Raised when argument conversion fails."""
    def __init__(self, value: str, target_type: str, original_error: Exception = None):
        self.value = value
        self.target_type = target_type
        self.original_error = original_error
        error_msg = f"Could not convert '{value}' to {target_type}"
        if original_error:
            error_msg += f": {original_error}"
        super().__init__(error_msg)
        
class ArgumentParsingError(ArgumentError):
    """Raised when there is a general argument parsing error."""
    def __init__(self, message: str = "Error parsing arguments"):
        super().__init__(message)

