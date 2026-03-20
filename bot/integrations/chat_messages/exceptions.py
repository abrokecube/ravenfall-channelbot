from bot.core.exceptions import ListenerError

class CheckFailure(ListenerError):
    """Raised when a listener check fails."""
    def __init__(self, message: str = "Check failed"):
        super().__init__(message)

