"""Remote callable mixin and decorator for inter-bot communication."""

from __future__ import annotations

import inspect
from inspect import Parameter
from typing import TYPE_CHECKING, Any, cast

from msgspec import Struct

if TYPE_CHECKING:
    from collections.abc import Callable

    from bot.core.components import Cog
    from bot.services.remote_bot_service import RemoteBotInstance

from bot.services.remote_bot_service import RemoteBotService

type RemoteMethod[T: Struct] = Callable[..., T]


class RemoteCallableMixin:
    """Mixin for Cogs to enable remote callable methods.

    Provides type hints for encoder/decoder hooks and enables
    proper type checking for classes with remote_callable methods.

    """

    def _remote_enc_hook(self, obj: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        """Encode object for JSON serialization.

        Override this method to provide custom encoding logic for
        specific types that msgspec doesn't handle by default.

        Args:
            obj: Object to encode

        Returns:
            Serialized representation of the object

        """
        __ = obj  # Override to implement custom encoding  # pyright: ignore[reportAny]
        return None

    def _remote_dec_hook(self, type_hint: type, obj: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        """Decode object from JSON deserialization.

        Override this method to provide custom decoding logic for
        specific types that msgspec doesn't handle by default.

        Args:
            type_hint: Expected type of the object
            obj: Object to decode

        Returns:
            Decoded object of the specified type

        """
        __ = type_hint  # Override to implement custom decoding
        __ = obj  # Override to implement custom decoding  # pyright: ignore[reportAny]
        return None


class RemoteCallable[T: Struct]:
    """Wrapper for remotely callable methods.

    Wraps a function to enable both local and remote execution.
    Local calls execute the function directly without HTTP overhead.
    Remote calls use the RemoteBotService to make HTTP requests to
    other bot instances.

    Type Parameters:
        T: Return type of the wrapped function

    Attributes:
        func: The original function
        return_type: The return type of the function
        cog_instance: The Cog instance this method belongs to
        enc_hook: Optional encoder hook override
        dec_hook: Optional decoder hook override
        _registered: Whether this method has been registered with RemoteBotService

    """

    def __init__(
        self,
        func: RemoteMethod[T],
        return_type: type[T],
        enc_hook: Callable[[Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
        dec_hook: Callable[[type, Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
    ) -> None:
        """Initialize the remote callable wrapper.

        Args:
            func: The function to wrap
            return_type: The return type of the function
            enc_hook: Optional encoder hook override
            dec_hook: Optional decoder hook override

        """
        self.func: Callable[..., T] = func
        self.return_type: type[T] = return_type
        self.cog_instance: Cog | None = None
        self.enc_hook: Callable[[Any], Any] | None = enc_hook  # pyright: ignore[reportExplicitAny]
        self.dec_hook: Callable[[type, Any], Any] | None = dec_hook  # pyright: ignore[reportExplicitAny]
        self._registered: bool = False
        self._method_name: str | None = None

        # list positional args in func

        sig = inspect.signature(func)
        self._positional_args_names: list[str] = [
            p.name
            for p in sig.parameters.values()
            if p.default == Parameter.empty  # pyright: ignore[reportAny]
        ]

    def __set_name__(self, owner: type, name: str) -> None:
        """Store the method name when assigned to a class.

        Args:
            owner: The class this is being assigned to
            name: The attribute name

        """
        self._method_name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> RemoteCallable[T]:  # pyright: ignore[reportExplicitAny, reportAny]
        """Get the descriptor instance.

        Args:
            obj: The instance this descriptor is accessed on
            objtype: The type of the instance

        Returns:
            The RemoteCallable instance with cog_instance set

        """
        if obj is None:
            return self
        self.cog_instance = obj
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> T:  # pyright: ignore[reportExplicitAny, reportAny]
        """Call the function locally without HTTP.

        Args:
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            The result of the function call

        """
        if self.cog_instance is None:
            msg = "RemoteCallable must be accessed through an instance"
            raise RuntimeError(msg)
        return self.func(self.cog_instance, *args, **kwargs)

    async def call_remote(
        self,
        remote_bot: RemoteBotInstance,
        *args: Any,  # pyright: ignore[reportExplicitAny, reportAny]
        **kwargs: Any,  # pyright: ignore[reportExplicitAny, reportAny]
    ) -> T:
        """Call the function on a remote bot via HTTP.

        Args:
            remote_bot: The remote bot instance to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            The deserialized result from the remote call

        Raises:
            RuntimeError: If the cog instance is not set
            ConnectionError: If the remote bot is unavailable

        """
        if self.cog_instance is None:
            msg = "RemoteCallable must be accessed through an instance"
            raise RuntimeError(msg)

        global_context = self.cog_instance.global_context
        remote_service = global_context.require_service(RemoteBotService)

        cog_name = self.cog_instance.__class__.__name__
        method_name = self._method_name

        if method_name is None:
            msg = "Method name not set for RemoteCallable"
            raise RuntimeError(msg)

        extended_kwargs = kwargs.copy()
        if args:
            extended_kwargs.update(
                dict(zip(self._positional_args_names, args, strict=True))
            )

        # Use provided hooks or fall back to class-level hooks
        enc_hook = self.enc_hook or getattr(self.cog_instance, "_remote_enc_hook", None)
        dec_hook = self.dec_hook or getattr(self.cog_instance, "_remote_dec_hook", None)

        return await remote_service.call_remote(
            remote_bot=remote_bot,
            cog_name=cog_name,
            method_name=method_name,
            return_type=self.return_type,
            kwargs=kwargs,
            enc_hook=enc_hook,
            dec_hook=dec_hook,
        )

    def _register(self) -> None:
        """Register this method with the RemoteBotService.

        This is called automatically on first access to ensure the method
        is available for remote calls.

        """
        if self._registered or self.cog_instance is None:
            return

        global_context = self.cog_instance.global_context
        remote_service = global_context.require_service(RemoteBotService)

        cog_name = self.cog_instance.__class__.__name__
        method_name = self._method_name

        if method_name is None:
            msg = "Method name not set for RemoteCallable"
            raise RuntimeError(msg)

        remote_service.register_method(
            cog_name=cog_name,
            method_name=method_name,
            bound_method=self.func.__get__(self.cog_instance, type(self.cog_instance)),
            return_type=self.return_type,
            cog_instance=self.cog_instance,
        )

        self._registered = True


def remote_callable[T: RemoteMethod[Struct], U: Struct](
    enc_hook: Callable[[Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
    dec_hook: Callable[[type, Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
) -> Callable[[T], RemoteCallable[U]]:
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

    def decorator(func: T) -> RemoteCallable[U]:
        """Inner decorator function.

        Args:
            func: The function to decorate

        Returns:
            A RemoteCallable wrapping the function

        """
        # Get return type from type hints
        return_annotation = inspect.signature(func).return_annotation  # pyright: ignore[reportAny]
        if return_annotation is inspect.Signature.empty:
            msg = f"Function {func.__name__} must have a return type annotation"
            raise TypeError(msg)

        return RemoteCallable[U](
            func=cast("RemoteMethod[U]", func),
            return_type=return_annotation,  # pyright: ignore[reportAny]
            enc_hook=enc_hook,
            dec_hook=dec_hook,
        )

    return decorator
