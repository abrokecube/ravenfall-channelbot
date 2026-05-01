import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, override

import psutil
from pydantic import BaseModel

from bot.core.components import BaseService
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigService

LOGGER = logging.getLogger(__name__)


class ProcessServiceConfig(BaseModel):
    """Process service config."""

    sandboxie_user_folder: str = f"C:\\Sandbox\\{os.getlogin()}"
    sandboxie_start_exe_path: str = "C:\\PROGRAM FILES\\SANDBOXIE-PLUS\\Start.exe"


class ShellResult(NamedTuple):
    """Result from running shell command."""

    code: int
    stdout: str | None


async def runshell(cmd: str) -> ShellResult:
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
    return ShellResult(code, out_text)


@dataclass(frozen=True)
class WatchedProcess:
    """Represents a process to be watched."""

    process_name: str
    box_name: str | None = None


@dataclass(frozen=True)
class ProcessStatistics:
    """Statistics for a process or group of processes."""

    cpu_usage_percent: float
    memory_rss_bytes: int
    memory_vms_bytes: int
    memory_percent: float
    uptime_seconds: float | None
    pids: set[int]


class ProcessManagerService(BaseService, ConfigSubscriberMixin):
    """Service to track processes that should be watched by the ProcessEventSource."""

    def __init__(self) -> None:
        super().__init__()
        self.watched_processes: set[WatchedProcess] = set()
        self.sandboxie_user_path: Path = Path(f"C:\\Sandbox\\{os.getlogin()}")
        self.sandboxie_start_path: Path = Path(
            "C:\\PROGRAM FILES\\SANDBOXIE-PLUS\\Start.exe"
        )

    @override
    async def setup(self) -> None:
        config_service = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_service)
        config = self.subscribe_config(
            "integrations.process_manager", ProcessServiceConfig
        )
        self._process_config(config)

    @override
    def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if not isinstance(config, ProcessServiceConfig):
            return
        self._process_config(config)

    def _process_config(self, config: ProcessServiceConfig):
        self.sandboxie_user_path = Path(config.sandboxie_user_folder)
        self.sandboxie_start_path = Path(config.sandboxie_start_exe_path)

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
    ) -> ShellResult:
        """Kills a process, natively or in Sandboxie."""
        if box_name:
            shellcmd = (
                f'"{self.sandboxie_start_path}" /box:{box_name} /silent /wait '
                f"taskkill /f /im {process_name}"
            )
            return await runshell(shellcmd)

        shellcmd = f"taskkill /f /im {process_name}"
        return await runshell(shellcmd)

    async def spawn_process(
        self,
        startup_command: str,
        box_name: str | None = None,
        working_dir: str | None = None,
    ) -> ShellResult:
        """Spawns a process, natively or in Sandboxie."""
        cmd_escaped = startup_command.replace('"', '\\"')

        # Prepend cd command if working_dir is provided
        if working_dir:
            cmd_escaped = f'cd /d "{working_dir}" && {cmd_escaped}'

        if box_name:
            shellcmd = (
                f'"{self.sandboxie_start_path}" '
                f'/box:{box_name} /silent /wait cmd /c "{cmd_escaped}"'
            )
            return await runshell(shellcmd)

        shellcmd = f'cmd /c "{cmd_escaped}"'
        return await runshell(shellcmd)

    async def get_process_statistics(
        self, process_name: str, box_name: str | None = None
    ) -> ProcessStatistics:
        """Get statistics for a process, natively or in Sandboxie.

        Args:
            process_name: The executable name (e.g., 'notepad.exe').
            box_name: The Sandboxie box name. If None, indicates a native host process.

        Returns:
            ProcessStatistics containing aggregated metrics across all matching instances.
        """
        target_name = process_name.lower()
        matching_pids: set[int] = set()

        if box_name:
            # Sandboxie process: get PIDs in the box first
            shellcmd = f'"{self.sandboxie_start_path}" /box:{box_name} /silent /listpids'
            code, text = await runshell(shellcmd)
            if code == 0 and text:
                box_pids: set[int] = set()
                for line in text.splitlines():
                    with contextlib.suppress(ValueError):
                        box_pids.add(int(line.strip()))

                # Filter by process name using psutil
                for pid in box_pids:
                    try:
                        proc = psutil.Process(pid)
                        if target_name in proc.name().lower():
                            matching_pids.add(pid)
                    except (
                        psutil.NoSuchProcess,
                        psutil.AccessDenied,
                        psutil.ZombieProcess,
                    ):
                        pass
        else:
            # Native process: search all processes
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if (
                        proc.info["pid"] is not None
                        and isinstance(proc.info["name"], str)
                        and target_name in proc.info["name"].lower()
                    ):
                        matching_pids.add(proc.info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

        if not matching_pids:
            return ProcessStatistics(
                cpu_usage_percent=0.0,
                memory_rss_bytes=0,
                memory_vms_bytes=0,
                memory_percent=0.0,
                uptime_seconds=None,
                pids=set(),
            )

        # Aggregate statistics across all matching PIDs
        total_cpu = 0.0
        total_rss = 0
        total_vms = 0
        total_percent = 0.0
        oldest_uptime: float | None = None
        current_time = time.time()

        for pid in matching_pids:
            try:
                proc = psutil.Process(pid)
                total_cpu += proc.cpu_percent()
                mem_info = proc.memory_info()
                total_rss += mem_info.rss
                total_vms += mem_info.vms
                total_percent += proc.memory_percent()

                create_time = proc.create_time()
                uptime = current_time - create_time
                if oldest_uptime is None or uptime > oldest_uptime:
                    oldest_uptime = uptime
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        return ProcessStatistics(
            cpu_usage_percent=total_cpu,
            memory_rss_bytes=total_rss,
            memory_vms_bytes=total_vms,
            memory_percent=total_percent,
            uptime_seconds=oldest_uptime,
            pids=matching_pids,
        )
