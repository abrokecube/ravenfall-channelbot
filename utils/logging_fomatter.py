# Modified from twitchio utils.py

from typing import Any
import logging
import logging.handlers
import os
import sys

def setup_logging(
    *,
    handler: logging.Handler | None = None,
    formatter: logging.Formatter | None = None,
    level: int | None = None,
    root: bool = True,
) -> None:
    """
    Set up logging with both console and rotating file handlers.
    
    Console handler: Shows logs at the specified level (default: INFO)
    File handler: Rotates logs when they reach 5MB, keeping 5 backup files, logs everything at DEBUG level
    
    Parameters:
        handler: Optional logging.Handler to use for console output
        formatter: Optional logging.Formatter to use for formatting log messages
        level: Logging level for console output (default: logging.INFO)
        root: If True, configure the root logger. If False, configure only the twitchio logger.
    """
    if level is None:
        level = logging.INFO
        
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    # Set up rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        'logs/ravenfall-bot.log',
        maxBytes=5*1024*1024,  # 5MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)  # Log everything to file
    
    # Set up console handler
    if handler is None:
        handler = logging.StreamHandler()
    handler.setLevel(level)  # Use the specified level for console
    
    # Apply formatter to both handlers
    if formatter is None:
        # Use color formatter for console if supported
        if isinstance(handler, logging.StreamHandler) and stream_supports_colour(handler.stream):
            formatter = ColourFormatter()
        else:
            formatter = logging.Formatter(
                '[{asctime}] [{levelname:<8}] {name}: {message}',
                datefmt='%Y-%m-%d %H:%M:%S', style="{"
            )
    
    # Always use a simple formatter for the file handler
    file_formatter = logging.Formatter(
        '[{asctime}] [{levelname:<8}] {name}: {message}',
        datefmt='%Y-%m-%d %H:%M:%S', style="{"
    )
    
    file_handler.setFormatter(file_formatter)
    handler.setFormatter(formatter)
    
    # Get the appropriate logger
    if root:
        logger = logging.getLogger()
    else:
        library, _, _ = __name__.partition(".")
        logger = logging.getLogger(library)
    
    # Remove all existing handlers
    for h in logger.handlers[:]:
        logger.removeHandler(h)
    
    # Add both handlers
    logger.addHandler(handler)
    logger.addHandler(file_handler)
    
    # Set the logger level to the lowest level of all handlers
    logger.setLevel(min(handler.level, file_handler.level))

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
