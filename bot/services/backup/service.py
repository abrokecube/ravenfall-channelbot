from __future__ import annotations

import asyncio
import datetime
import json
import logging
import tempfile
from pathlib import Path
from typing import override

from bot.core.components import BaseService
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigService

from .config import BackupConfig

LOGGER = logging.getLogger(__name__)


class BackupService(BaseService, ConfigSubscriberMixin):
    """Service for managing backups using Rclone."""

    def __init__(self) -> None:
        super().__init__()
        self._config: BackupConfig | None = None
        self._backup_task: asyncio.Task[None] | None = None

    @override
    async def setup(self) -> None:
        """Start the backup service and schedule the first backup."""
        config_service = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_service)
        self._config = self.subscribe_config(BackupConfig)
        self._start_backup_loop()

    @override
    async def on_config_changed(
        self,
        table: str,
        config: object,
        changed_fields: set[str],
    ) -> None:
        """Handle configuration changes."""
        if table == BackupConfig.config_table_name and isinstance(config, BackupConfig):
            self._config = config
            if "interval_seconds" in changed_fields:
                LOGGER.info("Backup interval changed, restarting loop.")
                self._start_backup_loop()

    def _start_backup_loop(self) -> None:
        """Start or restart the background backup loop."""
        if self._backup_task:
            __ = self._backup_task.cancel()

        if self._config and self._config.interval_seconds > 0:
            self._backup_task = asyncio.create_task(self._run_backup_loop())

    async def _run_backup_loop(self) -> None:
        """Background loop that triggers backups at regular intervals."""
        while True:
            try:
                if self._config:
                    await asyncio.sleep(self._config.interval_seconds)
                    await self.run_backup()
            except asyncio.CancelledError:
                break
            except Exception:
                LOGGER.exception("Error in backup loop")
                await asyncio.sleep(60)  # Wait a bit before retrying after error

    async def run_backup(self) -> None:
        """Execute the backup process for all registered items."""
        if not self._config or not self._config.registered_items:
            LOGGER.debug("No backup items registered, skipping.")
            return

        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d_%H-%M-%S")

        if self._config.remote_name:
            remote_prefix = f"{self._config.remote_name}:{self._config.remote_root}"
        else:
            remote_prefix = self._config.remote_root

        remote_root = f"{remote_prefix}/{timestamp}"

        LOGGER.info("Starting backup to %s", remote_root)

        for item in self._config.registered_items:
            dest = f"{remote_root}/{item.output_subpath}"
            cmd = [
                self._config.rclone_binary,
                "copy",
                item.input_path,
                dest,
            ]

            try:
                # Use run_in_executor for blocking subprocess call
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await process.communicate()

                if process.returncode == 0:
                    LOGGER.info("Successfully backed up: %s -> %s", item.input_path, dest)
                else:
                    LOGGER.error(
                        "Failed to backup %s: %s",
                        item.input_path,
                        stderr.decode().strip(),
                    )
            except Exception:
                LOGGER.exception("Failed to execute rclone for %s", item.input_path)

        await self._write_backup_index(remote_root)
        await self._cleanup_old_backups()

    async def _write_backup_index(self, remote_folder: str) -> None:
        """Write an index.json file to the remote backup folder."""
        if not self._config:
            return

        index_items: list[dict[str, str | bool | None]] = []
        for item in self._config.registered_items:
            path = Path(item.input_path)
            is_file = path.is_file()
            index_items.append(
                {
                    "input_path": item.input_path,
                    "output_subpath": item.output_subpath,
                    "is_file": is_file,
                    "name": path.name if is_file else None,
                }
            )

        index_data = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "items": index_items,
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp_file:
            json.dump(index_data, tmp_file, indent=2)
            tmp_path = tmp_file.name

        try:
            cmd = [
                self._config.rclone_binary,
                "copyto",
                tmp_path,
                f"{remote_folder}/index.json",
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            __ = await process.communicate()

            if process.returncode == 0:
                LOGGER.info("Successfully uploaded backup index to %s", remote_folder)
            else:
                LOGGER.error("Failed to upload backup index")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    async def _cleanup_old_backups(self) -> None:
        """Remove old backup folders beyond the folder limit."""
        if not self._config or self._config.folder_limit <= 0:
            return

        if self._config.remote_name:
            remote_base = f"{self._config.remote_name}:{self._config.remote_root}"
        else:
            remote_base = self._config.remote_root

        try:
            # List directories in the remote root
            cmd = [self._config.rclone_binary, "lsf", "--dirs-only", remote_base]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                LOGGER.error("Failed to list remote backups: %s", stderr.decode().strip())
                return

            folders = sorted(stdout.decode().splitlines())
            if len(folders) > self._config.folder_limit:
                folders_to_delete = folders[: len(folders) - self._config.folder_limit]
                for folder in folders_to_delete:
                    folder_path = f"{remote_base}/{folder}"
                    LOGGER.info("Deleting old backup: %s", folder_path)
                    purge_cmd = [self._config.rclone_binary, "purge", folder_path]
                    __ = await asyncio.create_subprocess_exec(
                        *purge_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
        except Exception:
            LOGGER.exception("Error during backup cleanup")

    async def register_path(self, input_path: str, _output_subpath: str) -> None:
        """Manually register a new backup item.

        Note: This currently only updates the in-memory state until the config is reloaded.
        In a full implementation, this might persist the change to config.toml.
        """
        # Implementation depends on how the bot handles dynamic config changes.
        # For now, we'll just log that it's added.
        LOGGER.warning("Dynamic registration of %s not fully implemented", input_path)
