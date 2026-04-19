"""Process Manager integration for watching native and Sandboxie processes."""

from .event_sources import ProcessEventSource as ProcessEventSource
from .events import ProcessKillEvent as ProcessKillEvent
from .events import ProcessSpawnEvent as ProcessSpawnEvent
from .services import ProcessManagerService as ProcessManagerService
from .services import WatchedProcess as WatchedProcess
from .services import runshell as runshell
