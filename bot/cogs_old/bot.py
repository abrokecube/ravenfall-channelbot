from ..commands import Context, TwitchContext, Commands, checks, parameter
from ..command_enums import UserRole, Platform
from ..command_utils import HasRole
from ..command_exceptions import CommandError
from ..commands_old.cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..ravenfallchannel import RFChannel
from ..process_watchdog_client import ProcessWatcherClient
from aiohttp import ClientResponseError
from utils.commands_rf import RFChannelConverter
from collections import defaultdict


class BotStuffCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, watcher_urls=["http://127.0.0.1:8110"], **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
        self.watchers: list[ProcessWatcherClient] = []
        for watcher_url in watcher_urls:
             self.watchers.append(ProcessWatcherClient(watcher_url))
    
    @Cog.command(name='sourcecode', aliases=["github", "source"])
    async def github_link(self, ctx: Context):
        """https://github.com/abrokecube/ravenfall-channelbot"""
        await ctx.reply("Source code on GitHub: https://github.com/abrokecube/ravenfall-channelbot")
    
    @Cog.command(name="reload_strings", aliases=["reloadstrings"])
    @parameter("all_", display_name="all", aliases=["a"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def reload_strings(self, ctx: Context, *, all_: bool = False, channel: RFChannel = 'this'):
        """Reloads Ravenfall bot translation strings.
        
        Args: 
            all: Reloads strings for all channels.
            channel: Channel to reload strings for.
        """        
        if all_:
            for _channel in self.rf_manager.channels:
                _channel.rfloc.load_definitions()
                _channel.rfloc.load_translations()
            await ctx.reply("Strings reloaded for all channels!")
            return

        channel.rfloc.load_definitions()
        channel.rfloc.load_translations()
        await ctx.reply("Strings reloaded!")
    
    @Cog.command(name="pausemon", aliases=["pausemonitoring", "pause_monitoring"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def pause_monitoring(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Pause channel monitoring
        
        Args: 
            channel: Channel to pause monitoring for.
        """
        if channel.monitoring_paused:
            await ctx.reply("Channel monitoring is already paused.")
            return
        channel.monitoring_paused = True
        await channel.stop()
        await ctx.reply("Channel monitoring paused.")
        
    @Cog.command(name="resumemon", aliases=["resumemonitoring", "resume_monitoring"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def resume_monitoring(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Resume channel monitoring
        
        Args:
            channel: Channel to resume monitoring for.
        """
        if not channel.monitoring_paused:
            await ctx.reply("Channel monitoring is not paused.")
            return
        channel.monitoring_paused = False
        await channel.start()
        await ctx.reply("Channel monitoring resumed.")
    
    @Cog.command(name="startproc", aliases=["startprocess", "start_process"])
    @parameter(name="process_name", greedy=True)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def start_process(self, ctx: Context, process_name: str):
        """Start a process
        
        Args:
            process_name: A registered process name.
        """
        for watcher in self.watchers:
            try:
                await watcher.start_process(process_name)
                await ctx.reply("Okay")
                break
            except ClientResponseError:
                continue
        else:
            raise CommandError("Failed to start process")
            
        
    @Cog.command(name="stopproc", aliases=["stopprocess", "stop_process"])
    @parameter(name="process_name", greedy=True)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def stop_process(self, ctx: Context, process_name: str):
        """Stop a process
        
        Args:
            process_name: A registered process name.
        """
        for watcher in self.watchers:
            try:
                await watcher.stop_process(process_name)
                await ctx.reply("Okay")
                break
            except ClientResponseError:
                continue
        else:
            raise CommandError("Failed to stop process")
            
        
    @Cog.command(name="restartproc", aliases=["restartprocess", "restart_processes"])
    @parameter(name="process_name", greedy=True)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def restart_process(self, ctx: Context, process_name: str):
        """Restart a process
        
        Args:
            process_name: A registered process name.
        """
        for watcher in self.watchers:
            try:
                await watcher.restart_process(process_name)
                await ctx.reply("Okay")
                break
            except ClientResponseError:
                continue
        else:
            raise CommandError("Failed to restart process")
        
        
    @Cog.command(name="listproc", aliases=["listprocess", "listprocesses", "list_processes"])
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def list_processes(self, ctx: Context):
        """List all registered processes."""
        try:
            processes = {}
            for watcher in self.watchers:
                watcher_procs = await watcher.get_processes()
                processes.update(watcher_procs)
                
            if not processes:
                await ctx.reply("There are no registered processes.")
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
            await ctx.reply(response)
        except ClientResponseError:
            raise CommandError("Failed to get processes")
        
    @Cog.command(name="pull", aliases=["pullproc", "pullprocess", "pull_process"])
    @parameter(name="process_name", greedy=True)
    @parameter(name="restart", aliases=['r'])
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def pull_process(self, ctx: Context, process_name: str, *, restart: bool = False):
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
                    await ctx.reply("Already up to date.")
                else:
                    commit_text = f"{latest_commit['hash'][:7]} - {latest_commit['author']}: {latest_commit['message']}"
                    if restart:
                        await ctx.reply(f"Latest commit: {commit_text} ✦ restarting...")
                        await watcher.restart_process(process_name)
                    else:
                        await ctx.reply(f"Okay ✦ latest commit: {commit_text}")
                break
            except (ClientResponseError, CommandError):
                continue
        else:
            raise CommandError("Failed to pull process")
        
    @Cog.command(name="reloadwatchdog")
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def reload_watchdog_conf(self, ctx: Context):
        for watcher in self.watchers:
            try:
                await watcher.reload_config()
            except Exception:
                continue
        await ctx.reply("Okay")
    

def setup(commands: Commands, **kwargs) -> None:
    commands.load_cog(BotStuffCog, **kwargs)
