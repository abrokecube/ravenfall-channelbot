from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, ClassVar, override

from pydantic import Field

from bot.clients.process_watchdog_client import ProcessWatcherClient, WatchdogClientError
from bot.core.components import Cog
from bot.integrations.chat_messages import UserRole, checks
from bot.integrations.commands import (  # noqa: TC001
    CommandError,
    CommandEvent,
    MinPermissionLevel,
    command,
    parameter,
)
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService

if TYPE_CHECKING:
    from bot.core.components import EventManager


class WatchdogCogSettings(ConfigModel):
    """Cog config."""

    config_table_name: ClassVar[str | None] = "cogs.watchdog"

    watcher_urls: list[str] = Field(default_factory=lambda: ["http://localhost:8110"])


class ProcessWatchdogCog(Cog, ConfigSubscriberMixin):
    """Process watchdog cog."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self.watchers: list[ProcessWatcherClient] = []

    @override
    async def setup(self) -> None:
        config_service = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_service)
        config = config_service.get_table(WatchdogCogSettings)
        __ = self.subscribe_config(WatchdogCogSettings)
        self.watchers = [ProcessWatcherClient(base_url=x) for x in config.watcher_urls]

    @override
    def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if not isinstance(config, WatchdogCogSettings):
            return
        self.watchers = [ProcessWatcherClient(base_url=x) for x in config.watcher_urls]

    @parameter(name="process_name", greedy=True)
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command(name="startproc", aliases=["startprocess", "start_process", "procstart"])
    async def start_process(self, ctx: CommandEvent, process_name: str):
        """Start a process.

        Args:
            ctx: Command context.
            process_name: A registered process name.
        """
        for watcher in self.watchers:
            try:
                __ = await watcher.start_process(process_name)
                await ctx.message.reply("Okay")
                break
            except WatchdogClientError:
                continue
        else:
            msg = "Failed to start process"
            raise CommandError(msg)

    @parameter(name="process_name", greedy=True)
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command(name="stopproc", aliases=["stopprocess", "stop_process", "procstop"])
    async def stop_process(self, ctx: CommandEvent, process_name: str):
        """Stop a process.

        Args:
            ctx: Command context.
            process_name: A registered process name.
        """
        for watcher in self.watchers:
            try:
                __ = await watcher.stop_process(process_name)
                await ctx.message.reply("Okay")
                break
            except WatchdogClientError:
                continue
        else:
            msg = "Failed to stop process"
            raise CommandError(msg)

    @parameter(name="process_name", greedy=True)
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command(
        name="restartproc", aliases=["restartprocess", "restart_processes", "procrestart"]
    )
    async def restart_process(self, ctx: CommandEvent, process_name: str):
        """Restart a process.

        Args:
            ctx: Command context.
            process_name: A registered process name.
        """
        for watcher in self.watchers:
            try:
                __ = await watcher.restart_process(process_name)
                await ctx.message.reply("Okay")
                break
            except WatchdogClientError:
                continue
        else:
            msg = "Failed to restart process"
            raise CommandError(msg)

    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command(
        name="listproc",
        aliases=["listprocess", "listprocesses", "list_processes", "proclist"],
    )
    async def list_processes(self, ctx: CommandEvent):
        """List all registered processes."""
        try:
            processes: dict[str, str] = {}
            for watcher in self.watchers:
                watcher_procs = await watcher.get_processes()
                processes.update(watcher_procs)

            if not processes:
                await ctx.message.reply("There are no registered processes.")
                return
            process_statuses: defaultdict[str, list[str]] = defaultdict(list)
            for name, status in processes.items():
                if status == "Running":
                    process_statuses["running"].append(name)
                elif status == "Stopped (Manual)":
                    process_statuses["stopped"].append(name)
                elif status == "Stopped":
                    process_statuses["not running"].append(name)
                else:
                    process_statuses["stopped"].append(name)
            out_str: list[str] = []
            for name, items in process_statuses.items():
                out_str.append(f"{name}: {', '.join(items)}")
            response = " - ".join(out_str)
            await ctx.message.reply(response)
        except WatchdogClientError as err:
            msg = "Failed to get processes"
            raise CommandError(msg) from err

    @parameter(name="process_name", greedy=True)
    @parameter(name="restart", aliases=["r"])
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command(name="pull", aliases=["pullproc", "pullprocess", "pull_process"])
    async def pull_process(
        self, ctx: CommandEvent, process_name: str, *, restart: bool = False
    ):
        """Runs "git pull" in a process' directory.

        Args:
            ctx: Command context.
            process_name: A registered process name.
            restart: Restart the process if a change has happened.
        """
        for watcher in self.watchers:
            try:
                result = await watcher.git_pull(process_name)
                if result.get("status", "") != "success":
                    msg = "Git returned an error"
                    raise CommandError(msg)
                latest_commit = result.get("latest_commit", None)
                if not latest_commit:
                    await ctx.message.reply("Already up to date.")
                else:
                    commit_text = (
                        f"{latest_commit['hash'][:7]} - "
                        f"{latest_commit['author']}: {latest_commit['message']}"
                    )
                    if restart:
                        await ctx.message.reply(
                            f"Latest commit: {commit_text} ✦ restarting..."
                        )
                        __ = await watcher.restart_process(process_name)
                    else:
                        await ctx.message.reply(f"Okay ✦ latest commit: {commit_text}")
                break
            except (WatchdogClientError, CommandError):
                continue
        else:
            msg = "Failed to pull process"
            raise CommandError(msg)

    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    @command(name="reloadwatchdog")
    async def reload_watchdog_conf(self, ctx: CommandEvent):
        """Reloads config for all watchdog instances."""
        had_errored = False
        for watcher in self.watchers:
            try:
                __ = await watcher.reload_config()
            except WatchdogClientError:
                had_errored = True
        if had_errored:
            msg = "One or more watchdogs failed to reload"
            raise CommandError(msg)
        await ctx.message.reply("Okay")
