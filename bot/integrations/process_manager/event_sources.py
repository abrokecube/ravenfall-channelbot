from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections import defaultdict
from typing import TYPE_CHECKING, Any, override

import psutil

from bot.core.components import BaseEventSource
from bot.integrations.process_manager.events import ProcessKillEvent, ProcessSpawnEvent
from bot.integrations.process_manager.services import (
    ProcessManagerService,
)
from utils.runshell import runshell

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from bot.core.components import EventManager
    from bot.integrations.process_manager.services import (
        WatchedProcess,
    )

LOGGER = logging.getLogger(__name__)


class ProcessEventSource(BaseEventSource):
    """Event source that polls for watched processes and emits spawn/kill events."""

    event_platform: str = "system"

    def __init__(self) -> None:
        super().__init__()
        self._polling_task: asyncio.Task[None] | None = None
        self._known_processes: dict[WatchedProcess, set[int]] = defaultdict(set)
        self._poll_interval: int = 5

    @override
    async def setup(self, event_manager: EventManager) -> None:
        await super().setup(event_manager)
        self._polling_task = asyncio.create_task(self._poll_loop())

    @override
    async def teardown(self) -> None:
        if self._polling_task:
            __ = self._polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._polling_task
        await super().teardown()

    async def _poll_loop(self) -> None:
        while True:
            try:
                service = self.global_context.get_service(ProcessManagerService)
                if service and service.watched_processes:
                    await self._scan_processes(service.watched_processes)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Error in ProcessEventSource poll loop")
            await asyncio.sleep(self._poll_interval)

    async def _scan_processes(self, watched: set[WatchedProcess]) -> None:
        # 1. Fetch system-wide mapping of pid -> name via psutil
        pid_to_name: dict[int, str] = {}
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["pid"] is not None and isinstance(proc.info["name"], str):
                    pid_to_name[proc.info["pid"]] = proc.info["name"].lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        # 2. Gather Sandboxie PIDs
        boxes_needed = {w.box_name for w in watched if w.box_name is not None}
        box_pids: dict[str, set[int]] = defaultdict(set)

        if boxes_needed:
            tasks: list[Coroutine[Any, Any, tuple[int, str | None]]] = []  # pyright: ignore[reportExplicitAny]
            ordered_boxes = list(boxes_needed)
            sandboxie_path = os.getenv("SANDBOXIE_START_PATH", "Start.exe")
            for box in ordered_boxes:
                shellcmd = f'"{sandboxie_path}" /box:{box} /silent /listpids'
                tasks.append(runshell(shellcmd))

            responses = await asyncio.gather(*tasks)
            for box, response in zip(ordered_boxes, responses, strict=True):
                if response:
                    _, text = response
                    if text:
                        for line in text.splitlines():
                            with contextlib.suppress(ValueError):
                                box_pids[box].add(int(line.strip()))

        # 3. Process each watched item
        for w in watched:
            active_pids: set[int] = set()
            target_name = w.process_name.lower()

            if w.box_name is None:
                # Native process: search in psutil
                for pid, name in pid_to_name.items():
                    if target_name in name:
                        active_pids.add(pid)
            else:
                # Sandboxie process: intersect psutil with box_pids
                for pid in box_pids[w.box_name]:
                    if pid in pid_to_name and target_name in pid_to_name[pid]:
                        active_pids.add(pid)

            # Compare to last known state
            last_pids = self._known_processes[w]

            # Spawns
            for new_pid in active_pids - last_pids:
                event = ProcessSpawnEvent(
                    data={
                        "process_name": w.process_name,
                        "box_name": w.box_name,
                        "pid": new_pid,
                    }
                )
                await self.send_event(event)

            # Kills
            for dead_pid in last_pids - active_pids:
                event = ProcessKillEvent(
                    data={
                        "process_name": w.process_name,
                        "box_name": w.box_name,
                        "pid": dead_pid,
                    }
                )
                await self.send_event(event)

            self._known_processes[w] = active_pids
