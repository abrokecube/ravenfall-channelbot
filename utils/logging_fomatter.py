# Modified from twitchio utils.py

from typing import Any
import logging
import os
import sys

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
