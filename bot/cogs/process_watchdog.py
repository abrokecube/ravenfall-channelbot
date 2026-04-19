from pydantic import BaseModel

from bot.core.components import Cog, EventManager
from bot.integrations.chat_messages.deco import checks
from bot.integrations.chat_messages.enums import UserRole
from bot.integrations.commands.checks import MinPermissionLevel
from bot.integrations.commands.deco import command, parameter
from bot.integrations.commands.events import CommandEvent
from bot.mixins.config_subscriber import ConfigSubscriberMixin


class CogSettings(BaseModel):
    """Cog config."""

    watcher_urls: list[str] = ["http://localhost:8110"]


class ProcessWatchdogCog(Cog, ConfigSubscriberMixin):
    """Process watchdog cog."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)

    @command(name="startproc", aliases=["startprocess", "start_process", "procstart"])
    @parameter(name="process_name", greedy=True)
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def start_process(self, ctx: CommandEvent, process_name: str):
        """Start a process

        Args:
            process_name: A registered process name.
        """
        for watcher in self.watchers:
            try:
                await watcher.start_process(process_name)
                await ctx.message.reply("Okay")
                break
            except ClientError:
                continue
        else:
            raise CommandError("Failed to start process")

    @command(name="stopproc", aliases=["stopprocess", "stop_process", "procstop"])
    @parameter(name="process_name", greedy=True)
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def stop_process(self, ctx: CommandEvent, process_name: str):
        """Stop a process

        Args:
            process_name: A registered process name.
        """
        for watcher in self.watchers:
            try:
                await watcher.stop_process(process_name)
                await ctx.message.reply("Okay")
                break
            except ClientError:
                continue
        else:
            raise CommandError("Failed to stop process")

    @command(
        name="restartproc", aliases=["restartprocess", "restart_processes", "procrestart"]
    )
    @parameter(name="process_name", greedy=True)
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def restart_process(self, ctx: CommandEvent, process_name: str):
        """Restart a process

        Args:
            process_name: A registered process name.
        """
        for watcher in self.watchers:
            try:
                await watcher.restart_process(process_name)
                await ctx.message.reply("Okay")
                break
            except ClientError:
                continue
        else:
            raise CommandError("Failed to restart process")

    @command(
        name="listproc",
        aliases=["listprocess", "listprocesses", "list_processes", "proclist"],
    )
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def list_processes(self, ctx: CommandEvent):
        """List all registered processes."""
        try:
            processes = {}
            for watcher in self.watchers:
                watcher_procs = await watcher.get_processes()
                processes.update(watcher_procs)

            if not processes:
                await ctx.message.reply("There are no registered processes.")
                return
            process_statuses = defaultdict(list)
            for name, status in processes.items():
                if status == "Running":
                    process_statuses["running"].append(name)
                elif status == "Stopped (Manual)":
                    process_statuses["stopped"].append(name)
                elif status == "Stopped":
                    process_statuses["not running"].append(name)
                else:
                    process_statuses["stopped"].append(name)
            out_str = []
            for name, items in process_statuses.items():
                out_str.append(f"{name}: {', '.join(items)}")
            response = " – ".join(out_str)
            await ctx.message.reply(response)
        except ClientError:
            raise CommandError("Failed to get processes")

    @command(name="pull", aliases=["pullproc", "pullprocess", "pull_process"])
    @parameter(name="process_name", greedy=True)
    @parameter(name="restart", aliases=["r"])
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def pull_process(
        self, ctx: CommandEvent, process_name: str, *, restart: bool = False
    ):
        """Runs "git pull" in a process' directory.

        Args:
            process_name: A registered process name.
            restart: Restart the process if a change has happened.
        """
        for watcher in self.watchers:
            try:
                result = await watcher.git_pull(process_name)
                if result.get("status", "") != "success":
                    raise CommandError("Git returned an error")
                latest_commit = result.get("latest_commit", None)
                if not latest_commit:
                    await ctx.message.reply("Already up to date.")
                else:
                    commit_text = f"{latest_commit['hash'][:7]} - {latest_commit['author']}: {latest_commit['message']}"
                    if restart:
                        await ctx.message.reply(
                            f"Latest commit: {commit_text} ✦ restarting..."
                        )
                        await watcher.restart_process(process_name)
                    else:
                        await ctx.message.reply(f"Okay ✦ latest commit: {commit_text}")
                break
            except (ClientError, CommandError):
                continue
        else:
            raise CommandError("Failed to pull process")

    @command(name="reloadwatchdog")
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def reload_watchdog_conf(self, ctx: CommandEvent):
        """Reloads config for all watchdog instances."""
        had_errored = False
        for watcher in self.watchers:
            try:
                await watcher.reload_config()
            except Exception:
                had_errored = True
        if had_errored:
            raise CommandError("One or more watchdogs failed to reload")
        await ctx.message.reply("Okay")
