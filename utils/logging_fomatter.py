# Modified from twitchio utils.py

from typing import Any
import logging
import os
import sys

def setup_logging(
    *,
    handler: logging.Handler | None = None,
    formatter: logging.Formatter | None = None,
    level: int | None = None,
    root: bool = True,
) -> None:
    """A helper function to setup logging for your application.

    Parameters
    ----------
    handler: :class:`logging.Handler` | None
        An optional :class:`logging.Handler` to use. Defaults to ``None``, which creates a :class:`logging.StreamHandler`
        by default.
    formatter: :class:`logging.Formatter` | None
        An optional :class:`logging.Formatter` to use. Defaults to ``None``, which uses a custom TrueColour
        formatter by default, falling back to standard colour support and finally no colour support if no colour
        is supported.
    level: int | None
        An optional int indicating the level of logging output. Defaults to ``20``, which is ``INFO``.
    root: bool
        An optional bool indicating whether logging should be setup on the root logger. When ``False``, logging will only be
        setup for twitchio. Defaults to ``True``.

    Examples
    --------

    .. code-block:: python3

        import logging

        import twitchio


        LOGGER: logging.Logger = logging.getLogger(__name__)
        twitchio.utils.setup_logging(level=logging.INFO)
        ...

        arg: str = "World!"
        LOGGER.info("Hello %s", arg)
    """
    if level is None:
        level = logging.INFO

    if handler is None:
        handler = logging.StreamHandler()

    if formatter is None:
        if isinstance(handler, logging.StreamHandler) and stream_supports_colour(handler.stream):  # type: ignore
            formatter = ColourFormatter()
        else:
            dt_fmt = "%Y-%m-%d %H:%M:%S"
            formatter = logging.Formatter("[{asctime}] [{levelname:<8}] {name}: {message}", dt_fmt, style="{")

    if root:
        logger = logging.getLogger()
    else:
        library, _, _ = __name__.partition(".")
        logger = logging.getLogger(library)

    handler.setFormatter(formatter)
    logger.setLevel(level)
    logger.addHandler(handler)  # type: ignore

def stream_supports_colour(stream: Any) -> bool:
    is_a_tty = hasattr(stream, "isatty") and stream.isatty()

    # Pycharm and Vscode support colour in their inbuilt editors
    if "PYCHARM_HOSTED" in os.environ or os.environ.get("TERM_PROGRAM") == "vscode":
        return is_a_tty

    if sys.platform != "win32":
        return is_a_tty

    # ANSICON checks for things like ConEmu
    # WT_SESSION checks if this is Windows Terminal
    return is_a_tty and ("ANSICON" in os.environ or "WT_SESSION" in os.environ)


def stream_supports_rgb(stream: Any) -> bool:
    if not stream_supports_colour(stream):
        return False

    if "COLORTERM" in os.environ:
        return os.environ["COLORTERM"] in ("truecolor", "24bit")

    return False

class ColourFormatter(logging.Formatter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self._handler: logging.StreamHandler[Any] = kwargs.get("handler", logging.StreamHandler())

        self._supports_colour: bool = stream_supports_colour(self._handler.stream)
        self._supports_rgb: bool = stream_supports_rgb(self._handler.stream)

        self._colours: dict[int, str] = {}
        self._RESET: str = "\033[0m"

        if self._supports_rgb:
            self._colours = {
                logging.DEBUG: "\x1b[40;1m",
                logging.INFO: "\x1b[38;2;100;55;215;1m",
                logging.WARNING: "\x1b[38;2;204;189;51;1m",
                logging.ERROR: "\x1b[38;2;161;38;46m",
                logging.CRITICAL: "\x1b[48;2;161;38;46",
            }

        elif self._supports_colour:
            self._colours = {
                logging.DEBUG: "\x1b[40;1m",
                logging.INFO: "\x1b[34;1m",
                logging.WARNING: "\x1b[33;1m",
                logging.ERROR: "\x1b[31m",
                logging.CRITICAL: "\x1b[41",
            }

        self._FORMATS: dict[int, logging.Formatter] = {
            level: logging.Formatter(
                f"\x1b[30;1m%(asctime)s\x1b[0m {colour}%(levelname)-8s\x1b[0m {colour}%(name)s\x1b[0m %(message)s"
            )
            for level, colour in self._colours.items()
        }

    def format(self, record: logging.LogRecord) -> str:
        formatter: logging.Formatter | None = self._FORMATS.get(record.levelno, None)
        if formatter is None:
            formatter = self._FORMATS[logging.DEBUG]

        # Override the traceback to always print in red
        if record.exc_info:
            text = formatter.formatException(record.exc_info)
            record.exc_text = f"\x1b[31m{text}\x1b[0m"

        output = formatter.format(record)

        # Remove the cache layer
        record.exc_text = None
        return output


ColorFormatter = ColourFormatter
