import asyncio
import logging
import os
from dataclasses import dataclass

from bot.core.components import BaseService

LOGGER = logging.getLogger(__name__)


async def runshell(cmd: str) -> tuple[int, str | None]:
    """Runs a shell command and returns the return code and stdout."""
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await proc.communicate()
    out_text = None
    LOGGER.debug(f"Command {cmd!r} exited with code {proc.returncode}")
    if stdout:
        stdout_text = stdout.decode()
        LOGGER.debug(f"Command stdout: {stdout_text.replace('\n', '\\n')}")
        out_text = stdout_text
    if stderr:
        LOGGER.error(f"Command stderr: {stderr.decode().replace('\n', '\\n')}")
    code = proc.returncode if proc.returncode is not None else 1
    return code, out_text


@dataclass(frozen=True)
class WatchedProcess:
    """Represents a process to be watched."""

    process_name: str
    box_name: str | None = None


class ProcessManagerService(BaseService):
    """Service to track processes that should be watched by the ProcessEventSource."""

    def __init__(self) -> None:
        super().__init__()
        self.watched_processes: set[WatchedProcess] = set()

    def watch_process(self, process_name: str, box_name: str | None = None) -> None:
        """Add a process to the watch list.

        Args:
            process_name: The executable name (e.g., 'notepad.exe').
            box_name: The Sandboxie box name. If None, indicates a native host process.
        """
        self.watched_processes.add(WatchedProcess(process_name, box_name))

    def unwatch_process(self, process_name: str, box_name: str | None = None) -> None:
        """Remove a process from the watch list."""
        p = WatchedProcess(process_name, box_name)
        if p in self.watched_processes:
            self.watched_processes.remove(p)

    async def kill_process(
        self, process_name: str, box_name: str | None = None
    ) -> tuple[int, str | None]:
        """Kills a process, natively or in Sandboxie."""
        if box_name:
            sandboxie_path = os.getenv("SANDBOXIE_START_PATH", "Start.exe")
            shellcmd = (
                f'"{sandboxie_path}" /box:{box_name} /silent /wait '
                f"taskkill /f /im {process_name}"
            )
            return await runshell(shellcmd)

        shellcmd = f"taskkill /f /im {process_name}"
        return await runshell(shellcmd)

    async def spawn_process(
        self, startup_command: str, box_name: str | None = None
    ) -> tuple[int, str | None]:
        """Spawns a process, natively or in Sandboxie."""
        cmd_escaped = startup_command.replace('"', '\\"')

        if box_name:
            sandboxie_path = os.getenv("SANDBOXIE_START_PATH", "Start.exe")
            shellcmd = (
                f'"{sandboxie_path}" /box:{box_name} /silent /wait cmd /c "{cmd_escaped}"'
            )
            return await runshell(shellcmd)

        shellcmd = f'cmd /c "{cmd_escaped}"'
        return await runshell(shellcmd)
