from ast import TypeVar
import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from msgspec import Struct

from .mixin import RemoteCallable
from .remote_bot_service import RemoteMethodAsync

if TYPE_CHECKING:
    from .mixin import RemoteMethod


def remote_callable[T: Callable[..., Awaitable[Struct]], U: Struct](
    return_type: type[U],
    enc_hook: Callable[[Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
    dec_hook: Callable[[type, Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
) -> Callable[[T], RemoteCallable[U, T]]:
    """Decorator to mark a method as remotely callable.

    This decorator transforms a function into a RemoteCallable object
    that can be called locally (without HTTP) or remotely (via HTTP
    to another bot instance).

    Args:
        enc_hook: Optional encoder hook to override class-level hook
        dec_hook: Optional decoder hook to override class-level hook

    Returns:
        A decorator function that returns a RemoteCallable

    Example:
        ```python
        class MyCog(Cog, RemoteCallableMixin):
            @remote_callable()
            def get_data(self) -> MyStruct:
                return MyStruct(value="hello")

            async def some_command(self, ctx):
                # Local call
                local = self.get_data()

                # Remote call
                remote_bot = self.global_context.get_service(RemoteBotService)
                    .get_remote_bot("bot2")
                remote = await self.get_data.call_remote(remote_bot)
        ```

    """

    def decorator(func: T) -> RemoteCallable[U, T]:
        """Inner decorator function.

        Args:
            func: The function to decorate

        Returns:
            A RemoteCallable wrapping the function

        """
        return RemoteCallable[U, T](
            func=func,
            return_type=return_type,
            enc_hook=enc_hook,
            dec_hook=dec_hook,
        )

    return decorator
