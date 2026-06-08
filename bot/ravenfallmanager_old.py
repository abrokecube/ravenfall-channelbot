from typing import TYPE_CHECKING, cast, override, Any
from collections.abc import Collection, Coroutine
from .ravenfallchannel_old import RFChannel
from .models_old import RFChannelEvent, Channel, RavenBotMessage, RavenfallMessage
from .ravenfall_query import GameMultiplier
from .multichat_command import (
    send_multichat_command,
    get_desync_info,
    get_total_item_count,
)
from . import ravenfall_middleman
from .messageprocessor import (
    MessageCallback,
    MessageProcessor,
    MessageMetadata,
    ClientInfo,
    BlockResponse,
)

if TYPE_CHECKING:
    from twitchAPI.chat import ChatMessage
from ravenpy import ExpMult
import asyncio
import aiohttp
import logging
from utils.routines import routine
from datetime import timedelta, datetime, timezone
import time
from .ravenfallrestarttask_old import RestartReason
from .prometheus import get_prometheus_instant
from utils.alert_monitor import BatchAlertMonitor
from utils.runshell import runshell
from async_lru import alru_cache
from .commands.global_context import GlobalContext

import os

logger = logging.getLogger(__name__)


class RFChannelManager:
    def __init__(self, config: Collection[Channel], global_context: GlobalContext):
        self.config: Collection[Channel] = config
        self.global_context: GlobalContext = global_context
        from ravenpy import RavenNest
        from twitchAPI.chat import Chat

        self.rfapi: RavenNest = global_context.require_service(RavenNest)
        self.chat: Chat = global_context.require_service(Chat)
        self.channels: list[RFChannel] = []
        self.channel_id_to_channel: dict[str, RFChannel] = {}
        self.channel_name_to_channel: dict[str, RFChannel] = {}
        self.ravennest_is_online: bool = True
        self.global_multiplier: float = 1.0
        self.global_multiplier_last_change: datetime = datetime.now(timezone.utc)

        self.rf_message_processor: MessageProcessor | None = None
        self.global_restart_lock: asyncio.Lock = asyncio.Lock()
        self.middleman_enabled: bool = False
        self.middleman_power_saving: bool = False
        self.middleman_connected: bool = False
        self.middleman_processor_server_client_count: int = 0
        self.load_channels()

        self.global_resync_lock: asyncio.Lock = asyncio.Lock()
        self.item_alert_monitor: "ItemAlertMonitor | None" = None
        self.ram_usage_alert_monitor: "RAMUsageAlertMonitor | None" = None

    def load_channels(self):
        for channel in self.config:
            self.channels.append(RFChannel(channel, self))
        for channel in self.channels:
            self.channel_id_to_channel[channel.channel_id] = channel
            self.channel_name_to_channel[channel.channel_name] = channel

    async def start(self):
        for channel in self.channels:
            await channel.start()
        _ = self.mult_check_routine.start()
        _ = self.resync_routine.start()
        _ = self.update_boosts_routine.start()
        msg_processor_host = os.getenv("RF_MIDDLEMAN_PROCESSOR_HOST", None)
        msg_processor_port = os.getenv("RF_MIDDLEMAN_PROCESSOR_PORT", None)
        if msg_processor_host and msg_processor_port:
            self.rf_message_processor = MessageProcessor(
                host=msg_processor_host,
                port=int(msg_processor_port),
            )
            self.rf_message_processor.start()
            self.rf_message_processor.add_message_callback(
                cast(MessageCallback, self.handle_processor_message)
            )
            self.rf_message_processor.add_connection_callback(self.on_processor_connect)
            self.rf_message_processor.add_disconnection_callback(
                self.on_processor_disconnect
            )
        else:
            logger.info(
                "RF_MIDDLEMAN_PROCESSOR_HOST or RF_MIDDLEMAN_PROCESSOR_PORT not set, not starting message processor"
            )
        self.item_alert_monitor = ItemAlertMonitor(self)
        self.ram_usage_alert_monitor = RAMUsageAlertMonitor(self)
        await self.item_alert_monitor.start()
        await self.ram_usage_alert_monitor.start()

    async def stop(self):
        for channel in self.channels:
            await channel.stop()
        self.mult_check_routine.cancel()
        self.resync_routine.cancel()
        if self.rf_message_processor:
            await self.rf_message_processor.astop()
        if self.item_alert_monitor:
            await self.item_alert_monitor.stop()
        if self.ram_usage_alert_monitor:
            await self.ram_usage_alert_monitor.stop()

    async def event_twitch_message(self, message: ChatMessage):
        for channel in self.channels:
            if message.room and channel.channel_id == message.room.room_id:
                await channel.event_twitch_message(message)

    async def handle_processor_message(
        self,
        message: RavenBotMessage | RavenfallMessage,
        metadata: MessageMetadata,
        _: ClientInfo,
    ) -> RavenfallMessage | RavenBotMessage | BlockResponse:
        out_message = message.copy()
        for channel in self.channels:
            if metadata.connection_id == channel.middleman_connection_id:
                if not metadata.is_api:
                    if metadata.source.lower() == "client":
                        __ = asyncio.create_task(
                            channel.event_ravenbot_message(cast(RavenBotMessage, message))
                        )
                    elif metadata.source.lower() == "server":
                        __ = asyncio.create_task(
                            channel.event_ravenfall_message(
                                cast(RavenfallMessage, message)
                            )
                        )
                    elif metadata.source.lower() in ("api-client", "api-server"):
                        pass
                    else:
                        logger.error(f"Unknown source: {metadata.source}")
                if metadata.source.lower() == "client":
                    out_message = await channel.process_ravenbot_message(
                        cast(RavenBotMessage, message.copy()), metadata
                    )
                elif metadata.source.lower() == "server":
                    out_message = await channel.process_ravenfall_message(
                        cast(RavenfallMessage, message.copy()), metadata
                    )
                elif metadata.source.lower() in ("api-client", "api-server"):
                    pass
                else:
                    logger.error(f"Unknown source: {metadata.source}")
                break
        else:
            logger.error(f"Unknown connection id: {metadata.connection_id}")
        if not out_message or out_message.get("block", False) == True:
            return {"block": True}
        return out_message

    async def on_processor_connect(self, _: ClientInfo):
        self.middleman_connected = True
        self.middleman_enabled = True
        self.middleman_processor_server_client_count += 1
        serverconf, err = await ravenfall_middleman.get_config()
        if not err:
            if serverconf:
                self.middleman_power_saving = not serverconf["disableTimeout"]
            else:
                self.middleman_power_saving = False

    async def on_processor_disconnect(self, _: ClientInfo):
        self.middleman_processor_server_client_count -= 1
        if self.middleman_processor_server_client_count <= 0:
            self.middleman_enabled = False

    def get_channel(
        self, *, channel_id: str | None = None, channel_name: str | None = None
    ) -> RFChannel | None:
        if channel_id:
            if channel_id in self.channel_id_to_channel:
                return self.channel_id_to_channel[channel_id]
        elif channel_name:
            if channel_name in self.channel_name_to_channel:
                return self.channel_name_to_channel[channel_name]
        return None

    @routine(delta=timedelta(seconds=45), max_attempts=99999)
    async def mult_check_routine(self):
        now = datetime.now(timezone.utc)
        old_online = self.ravennest_is_online
        is_online = False
        multiplier: ExpMult | None = None
        attempts = 3
        while attempts > 0:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                try:
                    async with session.get(
                        f"https://www.ravenfall.stream/api/game/exp-multiplier", ssl=False
                    ) as response:
                        data: GameMultiplier = cast(GameMultiplier, await response.json())
                        if data:
                            is_online = True
                            if data["multiplier"] != self.global_multiplier:
                                self.global_multiplier = data["multiplier"]
                                self.global_multiplier_last_change = now
                            multiplier = ExpMult(**data)
                        break
                except Exception as e:
                    logger.error(f"Can't connect to Ravenfall API: {e}")
            attempts -= 1
        self.ravennest_is_online = is_online

        if self.ravennest_is_online != old_online:
            if self.ravennest_is_online:
                msg = "🟢 RavenNest is online!"
            else:
                msg = "🔴 RavenNest is offline!"
            for channel in self.channels:
                await channel.send_chat_message(msg)

        if not self.ravennest_is_online:
            return
        if not multiplier or multiplier.multiplier <= 1:
            return
        if (now - self.global_multiplier_last_change) < timedelta(minutes=1, seconds=30):
            return
        for channel in self.channels:
            if channel.channel_restart_lock.locked():
                continue
            if channel.monitoring_paused:
                continue
            if (
                channel.multiplier
                and channel.multiplier["multiplier"] != self.global_multiplier
            ):
                logger.debug(
                    f"Multiplier mismatch for {channel.channel_name}: {channel.multiplier['multiplier']} != {self.global_multiplier}"
                )
                if channel.restart_task and channel.restart_task.get_time_left() > 120:
                    __ = channel.queue_restart(
                        90,
                        label="Town is desynced; multiplier is not updating",
                        reason=RestartReason.MULTIPLIER_DESYNC,
                    )
                try:
                    r = await send_multichat_command(
                        text=f"?say {channel.ravenbot_prefixes[0]}multiplier",
                        user_id=channel.channel_id,
                        user_name=channel.channel_name,
                        channel_id=channel.channel_id,
                        channel_name=channel.channel_name,
                    )
                except Exception as e:
                    await channel.send_chat_message(
                        f"?say {channel.ravenbot_prefixes[0]}multiplier"
                    )
                    logger.warning(
                        f"Failed to send multiplier command to {channel.channel_name}: {e}"
                    )

    @alru_cache(ttl=10)
    async def check_update_endpoint(self):
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            try:
                async with session.get(
                    f"https://www.ravenfall.stream/api/version/check", ssl=False
                ) as response:
                    __ = await response.text()
                    if response.status == 200:
                        return True
                    return False
            except Exception:
                return False

    async def get_desync_info(self) -> dict[str, float]:
        ch_desyncs: dict[str, float] = {}
        try:
            data = await get_desync_info()
        except Exception as e:
            logger.error(f"Failed to fetch desync info: {e}")
            return {}
        if time.time() - data["data"]["last_updated"] > 300:
            return ch_desyncs

        for channel_id in self.channel_id_to_channel.keys():
            if channel_id in data["data"]["towns"]:
                channel_name = self.channel_id_to_channel[channel_id].channel_name
                ch_desyncs[channel_name] = data["data"]["towns"][channel_id]
        return ch_desyncs

    async def get_total_item_count(self) -> dict[str, float]:
        total_item_data: dict[str, float] = {}
        try:
            data = await get_total_item_count()
        except Exception as e:
            logger.error(f"Failed to fetch total item count: {e}")
            return {}

        for channel_id in self.channel_id_to_channel.keys():
            if channel_id in data["data"]["towns"]:
                channel_name = self.channel_id_to_channel[channel_id].channel_name
                total_item_data[channel_name] = data["data"]["towns"][channel_id]
        return total_item_data

    @routine(delta=timedelta(seconds=60), max_attempts=99999)
    async def resync_routine(self):
        data = await self.get_desync_info()

        async def resync_task(channel: RFChannel):
            async with self.global_resync_lock:
                try:
                    r = await send_multichat_command(
                        text="?resync",
                        user_id=channel.channel_id,
                        user_name=channel.channel_name,
                        channel_id=channel.channel_id,
                        channel_name=channel.channel_name,
                    )
                except Exception as e:
                    await channel.send_chat_message("?resync")
                    logger.warning(
                        f"Failed to send resync command to {channel.channel_name}: {e}"
                    )
                await asyncio.sleep(60)

        tasks: list[Coroutine[Any, Any, None]] = []
        for channel_name, desync in data.items():
            if not self.ravennest_is_online:
                continue
            if abs(desync) < 30:  # 30 seconds
                continue
            channel = self.channel_name_to_channel[channel_name]
            if channel.monitoring_paused:
                continue
            if channel.event == RFChannelEvent.DUNGEON:
                continue
            if channel.channel_restart_lock.locked():
                continue
            if channel.channel_post_restart_lock.locked():
                continue
            tasks.append(resync_task(channel))
        if tasks:
            __ = await asyncio.gather(*tasks)

    @routine(delta=timedelta(hours=3), wait_first=True, max_attempts=99999)
    async def update_boosts_routine(self):
        for channel in self.channels:
            if channel.channel_restart_lock.locked():
                async with channel.channel_restart_lock:
                    return
            while True:
                if not await channel.update_boost():
                    await asyncio.sleep(30)
                else:
                    break
            await asyncio.sleep(120)


class ItemAlertMonitor(BatchAlertMonitor):
    def __init__(self, rfmanager: RFChannelManager):
        super().__init__(
            interval=30, timeout=3 * 60, alert_interval=60 * 60, name="ItemAlertMonitor"
        )
        self.rfmanager: RFChannelManager = rfmanager
        self.last_counts: dict[str, float] = {}

    @override
    async def check_condition(self) -> dict[str, bool | str | tuple[bool, str]]:
        items = await self.rfmanager.get_total_item_count()
        alerts: dict[str, bool | str | tuple[bool, str]] = {}
        for channel_name, item_count in items.items():
            last_count = self.last_counts.get(channel_name, item_count - 1)
            logger.debug(
                f"[ItemAlertMonitor] {channel_name} items: {item_count} (last: {last_count})"
            )
            self.last_counts[channel_name] = item_count
            is_alerting = item_count == last_count
            if is_alerting and self.rfmanager.ravennest_is_online:
                alerts[channel_name] = "No item gain"
            else:
                alerts[channel_name] = True
        return alerts

    @override
    async def trigger_alert(self, name: str, reason: str):
        if reason == "No item gain":
            channel = self.rfmanager.get_channel(channel_name=name)
            if channel and not channel.monitoring_paused:
                __ = channel.queue_restart(
                    90,
                    label="Town is desynced; items stopped getting rewarded",
                    reason=RestartReason.ITEM_DESYNC,
                )

    @override
    async def resolve_alert(self, name: str):
        pass


class RAMUsageAlertMonitor(BatchAlertMonitor):
    def __init__(self, rfmanager: RFChannelManager):
        super().__init__(
            interval=60,
            timeout=10 * 60,
            alert_interval=60 * 60,
            name="RAMUsageAlertMonitor",
        )
        self.rfmanager: RFChannelManager = rfmanager

    @override
    async def check_condition(self) -> dict[str, bool | str | tuple[bool, str]]:
        working_set = await get_prometheus_instant(
            "windows_process_working_set_private_bytes{process='Ravenfall'}"
        )
        if not working_set:
            return {}
        tasks = []
        for ch in self.rfmanager.channels:
            shellcmd: str = f'"{os.getenv("SANDBOXIE_START_PATH")}" /box:{ch.sandboxie_box} /silent /listpids'
            tasks.append(runshell(shellcmd))
        responses: list[tuple[int | None, str]] = await asyncio.gather(*tasks)
        pid_lists = [x.splitlines() for code, x in responses if x]
        box_pids: dict[str, list[str]] = {}
        for i in range(len(self.rfmanager.channels)):
            box_pids[self.rfmanager.channels[i].channel_name] = (
                pid_lists[i] if i < len(pid_lists) else []
            )
        processes: dict[str, float] = {}
        total_bytes = 0
        for metric in working_set:
            m = metric["metric"]
            pid = m.get("process_id")  # type: ignore
            if not pid:
                continue
            bytes_usage = int(metric["value"][1])
            total_bytes += bytes_usage
            processes[pid] = bytes_usage
        processes_named: dict[str, float] = {}
        for name, pids in box_pids.items():
            for pid in pids:
                if pid in processes:
                    processes_named[name] = processes[pid]
                    break
        alerts: dict[str, bool | str | tuple[bool, str]] = {}
        maximum_total_ravenfall_bytes = (
            int(os.getenv("MAX_RAVENFALL_TOTAL_MIB", "10240")) * 1024 * 1024
        )
        maximum_single_ravenfall_bytes = (
            int(os.getenv("MAX_RAVENFALL_MIB", "5120")) * 1024 * 1024
        )
        over_bytes = max(0, total_bytes - maximum_total_ravenfall_bytes)
        logger.debug(
            f"[RAMUsageAlertMonitor] Total usage: {total_bytes / 1024 / 1024} MiB ({over_bytes / 1024 / 1024} MiB over)"
        )
        for name, bytes_used in sorted(
            processes_named.items(), key=lambda x: x[1], reverse=True
        ):
            logger.debug(
                f"[RAMUsageAlertMonitor] {name} usage: {bytes_used / 1024 / 1024} MiB"
            )
            if over_bytes > 0:
                alerts[name] = "Over maximum total RAM usage"
                over_bytes -= bytes_used
            elif bytes_used > maximum_single_ravenfall_bytes:
                alerts[name] = "Over maximum RAM usage"
            else:
                alerts[name] = True
        return alerts

    @override
    async def trigger_alert(self, name: str, reason: str):
        if reason:
            channel = self.rfmanager.get_channel(channel_name=name)
            if channel and not channel.monitoring_paused:
                __ = channel.queue_restart(
                    90,
                    label="Town is using too much memory",
                    reason=RestartReason.MEMORY_USE,
                )

    @override
    async def resolve_alert(self, name: str):
        pass
