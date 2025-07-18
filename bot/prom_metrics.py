from enum import Enum
from typing import Dict, NamedTuple

class UndefinedMetric(Exception):
    def __init__(self):
        super().__init__("bruh")

class AlreadyExists(Exception):
    def __init__(self):
        super().__init__("bruh")

class MetricType(Enum):
    COUNTER = 0
    GAUGE = 1

class MetricDefinition(NamedTuple):
    name: str
    description: str
    metric_type: MetricType

class MetricEntry(NamedTuple):
    name: str
    labels: str

ababab = str.maketrans({
    '"': '\\"',
    '\\': '\\\\',
    '\b': '\\b',
    '\f': '\\f',
    '\n': '\\n',
    '\r': '\\r',
    '\t': '\\t',
})
def to_label(obj):
    if isinstance(obj, bool):
        return "true" if obj else "false"
    elif isinstance(obj, str):
        return obj.translate(ababab)
    else:
        return str(obj)

class Metrics:
    def __init__(self):
        self.definitions: Dict[str, MetricDefinition] = {}
        self.metrics: Dict[MetricEntry, float] = {}
        
    def add_value(self, metric_name: str, value: float | int | bool, **labels):
        if value is None:
            print(f"Empty metric: {metric_name}, {labels}")
            return
        # b = ','.join([f'{x}=\"{json.dumps(y).strip('"')}\"' for x, y in labels.items()])
        b = ','.join([f'{x}=\"{to_label(y)}\"' for x, y in labels.items()])
                      
        a = MetricEntry(metric_name, b)
        # if a in self.metrics:
        #     raise AlreadyExists()
        # if not a.name in self.definitions:
        #     raise UndefinedMetric()
        if isinstance(value, bool):
            self.metrics[a] = 1 if value else 0
        self.metrics[a] = float(value)
    
    def add_def(self, metric_name: str, description: str, type: MetricType=MetricType.GAUGE, *, value: float | int | bool = None, **labels):
        # if metric_name in self.definitions:
        #     raise AlreadyExists()
        self.definitions[metric_name] = MetricDefinition(metric_name, description, type)
        if value is not None:
            self.add_value(metric_name, value, **labels)
    
    def get_text(self):
        # metrics = sorted(list(self.metrics.keys()), key=lambda x: x.name)
        metrics = list(self.metrics.keys())
        defs = set(self.definitions.keys())
        out_text = []
        for m in metrics:
            if m.name in defs:
                metric_def = self.definitions[m.name]
                out_text.extend([
                    f"# HELP {m.name} {metric_def.description}",
                    f"# TYPE {m.name} {metric_def.metric_type.name.lower()}",
                ])
                defs.remove(m.name)
            value = self.metrics[m]
            labels = m.labels
            if labels:
                labels = "{%s}" % labels
            out_text.append(
                f"{m.name}{labels} {value}"
            )
        return "\n".join(out_text)

from typing import TYPE_CHECKING, List
if TYPE_CHECKING:
    from .ravenfallmanager import RFChannelManager
import psutil
import os
import asyncio
from utils.runshell import runshell

class MetricsManager:
    def __init__(self, rf_manager: 'RFChannelManager'):
        self.rf_manager = rf_manager

    async def desync_info(self, m: Metrics):
        desync_info = await self.rf_manager.get_desync_info()
        m.add_def("rf_ext_desync_seconds", "Estimated desync time in seconds", MetricType.GAUGE)
        for channel_name, desync in desync_info.items():
            m.add_value("rf_ext_desync_seconds", desync, channel=channel_name)

    async def ravenfall_pids(self, m: Metrics):
        ravenfall_pids = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                # Check if the process name matches (case-insensitive)
                if "ravenfall" in proc.info['name'].lower():
                    ravenfall_pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        tasks = []
        for ch in self.rf_manager.channels:
            shellcmd = (
                f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{ch.sandboxie_box} /silent /listpids"
            )
        tasks.append(runshell(shellcmd))
        responses: List[str | None] = await asyncio.gather(*tasks)
        pid_lists = [x.splitlines() for code, x in responses]
        box_pids = {}
        for i in range(len(self.rf_manager.channels)):
            box_pids[self.rf_manager.channels[i].channel_name] = pid_lists[i]

        m.add_def("rf_ext_ravenfall_info", "Information about ravenfall", MetricType.GAUGE)
        for ch in self.rf_manager.channels:
            for pid in box_pids[ch.channel_name]:
                if pid in ravenfall_pids:
                    m.add_value("rf_ext_ravenfall_info", 1, channel=ch.channel_name, process_id=pid)

    async def get_metrics(self) -> str:
        m = Metrics()
        tasks = [
            self.desync_info(m),
            self.ravenfall_pids(m)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        return m.get_text()

        