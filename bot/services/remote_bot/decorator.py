from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from .mixin import RemoteCallable

if TYPE_CHECKING:
    import types


def remote_callable[T: Callable[..., Awaitable[object]], U](
    return_type: type[U] | types.UnionType,
    enc_hook: Callable[[Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
    dec_hook: Callable[[type, Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
) -> Callable[[T], RemoteCallable[U, T]]:
    """Decorator to mark a method as remotely callable.

    This decorator transforms a function into a RemoteCallable object
    that can be called locally (without HTTP) or remotely (via HTTP
    to another bot instance).

    Args:
        return_type: The return type of the function (can be a type or union type)
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
