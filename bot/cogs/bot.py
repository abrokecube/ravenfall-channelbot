from __future__ import annotations

from ..process_watchdog_client import ProcessWatcherClient

from ..commands.cog import Cog
from ..commands.decorators import (
    command, parameter, checks
)
from ..commands.checks import MinPermissionLevel
from ..commands.converters import RFChannelConverter
from ..commands.enums import UserRole
from ..commands.events import CommandEvent
from ..commands.exceptions import (
    CommandError
)
from aiohttp import ClientError
from collections import defaultdict

from typing import TYPE_CHECKING, NamedTuple, Any, List, Dict
from ..ravenfallchannel import RFChannel

class BotStuffCog(Cog):
    def __init__(self, event_manager, watcher_urls=["http://127.0.0.1:8110"]):
        super().__init__(event_manager)
        
        self.watchers: list[ProcessWatcherClient] = []
        for watcher_url in watcher_urls:
             self.watchers.append(ProcessWatcherClient(watcher_url))
             
    @command(name='sourcecode', aliases=["github", "source"])
    async def github_link(self, ctx: CommandEvent):
        """https://github.com/abrokecube/ravenfall-channelbot"""
        await ctx.message.reply("Source code on GitHub: https://github.com/abrokecube/ravenfall-channelbot")

    @command(name="reload_strings", aliases=["reloadstrings"])
    @parameter("all_", display_name="all", aliases=["a"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def reload_strings(self, ctx: CommandEvent, *, all_: bool = False, channel: RFChannel = 'this'):
        """Reloads Ravenfall bot translation strings.
        
        Args: 
            all: Reloads strings for all channels.
            channel: Channel to reload strings for.
        """
        rf_manager = self.global_context.ravenfall_manager
        if all_:
            for _channel in rf_manager.channels:
                _channel.rfloc.load_definitions()
                _channel.rfloc.load_translations()
            await ctx.message.reply("Strings reloaded for all channels!")
            return

        channel.rfloc.load_definitions()
        channel.rfloc.load_translations()
        await ctx.message.reply("Strings reloaded!")
    
    @command(name="pausemon", aliases=["pausemonitoring", "pause_monitoring"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def pause_monitoring(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Pause channel monitoring
        
        Args: 
            channel: Channel to pause monitoring for.
        """
        if channel.monitoring_paused:
            await ctx.message.reply("Channel monitoring is already paused.")
            return
        channel.monitoring_paused = True
        await channel.stop()
        await ctx.message.reply("Channel monitoring paused.")
        
    @command(name="resumemon", aliases=["resumemonitoring", "resume_monitoring"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def resume_monitoring(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Resume channel monitoring
        
        Args:
            channel: Channel to resume monitoring for.
        """
        if not channel.monitoring_paused:
            await ctx.message.reply("Channel monitoring is not paused.")
            return
        channel.monitoring_paused = False
        await channel.start()
        await ctx.message.reply("Channel monitoring resumed.")
    
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
            
        
    @command(name="restartproc", aliases=["restartprocess", "restart_processes", "procrestart"])
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
        
        
    @command(name="listproc", aliases=["listprocess", "listprocesses", "list_processes", "proclist"])
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
        except (ClientError):
            raise CommandError("Failed to get processes")
        
    @command(name="pull", aliases=["pullproc", "pullprocess", "pull_process"])
    @parameter(name="process_name", greedy=True)
    @parameter(name="restart", aliases=['r'])
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def pull_process(self, ctx: CommandEvent, process_name: str, *, restart: bool = False):
        """Runs "git pull" in a process's directory.
        
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
                        await ctx.message.reply(f"Latest commit: {commit_text} ✦ restarting...")
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
        had_errored = False
        for watcher in self.watchers:
            try:
                await watcher.reload_config()
            except Exception:
                had_errored = True
        if had_errored:
            raise CommandError("One or more watchdogs failed to reload")
        await ctx.message.reply("Okay")
