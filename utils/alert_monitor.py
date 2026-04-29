import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from utils.format_time import format_seconds

logger = logging.getLogger(__name__)


@dataclass
class AlertConfig:
    interval: float
    timeout: float
    alert_interval: float


@dataclass
class AlertStatus:
    running: bool
    is_alerting: bool
    failure_duration_seconds: float
    config: AlertConfig


class AlertMonitor(ABC):
    """A base class for monitoring a condition and triggering an alert if the condition
    remains in a 'bad' state (returns False) for a specified duration.
    """

    def __init__(
        self,
        interval: float,
        timeout: float,
        alert_interval: float | None = None,
        name: str = "AlertMonitor",
    ):
        """Initialize the AlertMonitor.

        Args:
            interval (float): The interval in seconds between checks.
            timeout (float): The duration in seconds the condition must be False
                             before alerting starts.
            alert_interval (float, optional): The interval in seconds between alerts
                                              once in the alerting state. Defaults to interval.
            name (str, optional): The name of this monitor for logging. Defaults to "AlertMonitor".
        """
        self.name: str = name
        self.interval: float = interval
        self.timeout: float = timeout
        self.alert_interval: float = (
            alert_interval if alert_interval is not None else interval
        )
        self._running: bool = False
        self._task: asyncio.Task[Any] | None = None  # pyright: ignore [reportExplicitAny]
        self._first_failure_time: float | None = None
        self._last_alert_time: float | None = None
        self._is_alerting: bool = False

    def get_status(self) -> AlertStatus:
        """Get the current status of the monitor.

        Returns:
            AlertStatus: An object containing the current status.
        """
        duration = 0.0
        if self._first_failure_time is not None:
            try:
                loop = asyncio.get_running_loop()
                duration = loop.time() - self._first_failure_time
            except RuntimeError:
                pass

        return AlertStatus(
            running=self._running,
            is_alerting=self._is_alerting,
            failure_duration_seconds=duration,
            config=AlertConfig(
                interval=self.interval,
                timeout=self.timeout,
                alert_interval=self.alert_interval,
            ),
        )

    @abstractmethod
    async def check_condition(self) -> bool | str | tuple[bool, str]:
        """Check the condition.

        Returns:
            bool: True if the condition is normal/good.
                  False if the condition is bad (starts the timer).
        """

    @abstractmethod
    async def trigger_alert(self, reason: str):
        """Trigger the alert. This method is called every interval while the
        condition remains False after the timeout has passed.

        Args:
            reason (str): The reason for the alert (e.g., exception message or failure description).
        """

    @abstractmethod
    async def resolve_alert(self):
        """Called when the condition returns to normal after an alert has been triggered."""

    async def start(self):
        """Start the monitoring loop."""
        if self._running:
            logger.warning(f"[{self.name}] AlertMonitor is already running.")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"[{self.name}] AlertMonitor started.")
        logger.debug(
            f"[{self.name}] configuration: interval={self.interval}, timeout={self.timeout}, alert_interval={self.alert_interval}"
        )

    async def stop(self):
        """Stop the monitoring loop."""
        if not self._running:
            return

        self._running = False
        logger.debug(f"[{self.name}] stopping monitor, cancelling task")
        if self._task:
            _ = self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(f"[{self.name}] AlertMonitor stopped.")

    async def _run_loop(self):
        """The main monitoring loop."""
        try:
            while self._running:
                reason = "Condition check returned False"
                try:
                    is_good = await self.check_condition()
                    logger.debug(f"[{self.name}] check_condition returned {is_good}")
                    if isinstance(is_good, tuple):
                        is_good, reason = is_good
                    elif isinstance(is_good, str):
                        reason = is_good
                        is_good = False
                except Exception:
                    logger.exception(
                        f"[{self.name}] Error in check_condition",
                    )
                    # Assume bad state on error
                    is_good = False
                    reason = "Check failed with error"

                now = asyncio.get_running_loop().time()

                if is_good:
                    logger.debug(
                        f"[{self.name}] condition good, is_alerting={self._is_alerting}"
                    )
                    # Condition is good (True)
                    if self._is_alerting:
                        logger.info(
                            f"[{self.name}] Condition returned to normal. Resolving alert."
                        )
                        try:
                            await self.resolve_alert()
                        except Exception:
                            logger.exception(
                                f"[{self.name}] Error in resolve_alert",
                            )
                    elif self._first_failure_time is not None:
                        elapsed = now - self._first_failure_time
                        logger.info(
                            f"[{self.name}] Condition returned to normal before alert triggered after {format_seconds(elapsed)}."
                        )

                    self._first_failure_time = None
                    self._last_alert_time = None
                    self._is_alerting = False
                # Condition is bad (False)
                elif self._first_failure_time is None:
                    self._first_failure_time = now
                    # Timer starts
                else:
                    elapsed = now - self._first_failure_time
                    if elapsed >= self.timeout:
                        # Timer ran out, trigger alert
                        if not self._is_alerting:
                            logger.warning(
                                f"[{self.name}] Alert timer expired. Starting alerts."
                            )
                            self._is_alerting = True

                        if (
                            self._last_alert_time is None
                            or (now - self._last_alert_time) >= self.alert_interval
                        ):
                            try:
                                logger.info(f"[{self.name}] Triggering alert: {reason}")
                                await self.trigger_alert(reason)
                                self._last_alert_time = now
                            except Exception:
                                logger.exception(
                                    f"[{self.name}] Error in trigger_alert",
                                )

                await asyncio.sleep(self.interval)

        except asyncio.CancelledError:
            logger.info(f"[{self.name}] AlertMonitor loop cancelled.")
            raise
        except Exception:
            logger.critical(
                f"[{self.name}] AlertMonitor loop crashed",
            )
            self._running = False


@dataclass
class MonitorState:
    first_failure_time: float | None = None
    last_alert_time: float | None = None
    is_alerting: bool = False


class BatchAlertMonitor(ABC):
    """A base class for monitoring multiple conditions in a batch and triggering alerts
    independently for each condition.
    """

    def __init__(
        self,
        interval: float,
        timeout: float,
        alert_interval: float | None = None,
        name: str = "BatchAlertMonitor",
    ):
        """Initialize the BatchAlertMonitor.

        Args:
            interval (float): The interval in seconds between checks.
            timeout (float): The duration in seconds a condition must be False
                             before alerting starts.
            alert_interval (float, optional): The interval in seconds between alerts
                                              once in the alerting state. Defaults to interval.
            name (str, optional): The name of this monitor for logging. Defaults to "BatchAlertMonitor".
        """
        self.name: str = name
        self.interval: float = interval
        self.timeout: float = timeout
        self.alert_interval: float = (
            alert_interval if alert_interval is not None else interval
        )
        self._running: bool = False
        self._task: asyncio.Task[Any] | None = None  # pyright: ignore [reportExplicitAny]
        self._states: dict[str, MonitorState] = {}

    def get_status(self) -> dict[str, AlertStatus]:
        """Get the current status of all monitors.

        Returns:
            dict[str, AlertStatus]: A dictionary mapping alert names to their status.
        """
        statuses: dict[str, AlertStatus] = {}
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        for name, state in self._states.items():
            duration = 0.0
            if state.first_failure_time is not None and loop:
                duration = loop.time() - state.first_failure_time

            statuses[name] = AlertStatus(
                running=self._running,
                is_alerting=state.is_alerting,
                failure_duration_seconds=duration,
                config=AlertConfig(
                    interval=self.interval,
                    timeout=self.timeout,
                    alert_interval=self.alert_interval,
                ),
            )
        return statuses

    @abstractmethod
    async def check_condition(self) -> dict[str, bool | str | tuple[bool, str]]:
        """Check the conditions.

        Returns:
            dict[str, bool | str | tuple[bool, str]]: A dictionary mapping alert names to their status.
                - True: Good condition
                - False: Bad condition (default reason)
                - str: Bad condition (string is reason)
                - tuple(bool, str): Explicit status and reason
        """

    @abstractmethod
    async def trigger_alert(self, name: str, reason: str):
        """Trigger an alert for a specific condition.

        Args:
            name (str): The name of the alert.
            reason (str): The reason for the alert.
        """

    @abstractmethod
    async def resolve_alert(self, name: str):
        """Called when a specific condition returns to normal after an alert has been triggered.

        Args:
            name (str): The name of the alert.
        """

    async def start(self):
        """Start the monitoring loop."""
        if self._running:
            logger.warning(f"[{self.name}] BatchAlertMonitor is already running.")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"[{self.name}] BatchAlertMonitor started.")
        logger.debug(
            f"[{self.name}] configuration: interval={self.interval}, timeout={self.timeout}, alert_interval={self.alert_interval}"
        )

    async def stop(self):
        """Stop the monitoring loop."""
        if not self._running:
            return

        self._running = False
        logger.debug(f"[{self.name}] stopping batch monitor, cancelling task")
        if self._task:
            _ = self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info(f"[{self.name}] BatchAlertMonitor stopped.")

    async def _run_loop(self):
        """The main monitoring loop."""
        try:
            while self._running:
                try:
                    results = await self.check_condition()
                    logger.debug(
                        f"[{self.name}] check_condition returned keys: {list(results.keys())}"
                    )
                except Exception:
                    logger.exception(
                        f"[{self.name}] Error in check_condition",
                    )
                    results = {}  # Or handle global failure?

                now = asyncio.get_running_loop().time()

                # Process results
                all_keys = set(results.keys()) | set(self._states.keys())
                logger.debug(f"[{self.name}] processing keys: {all_keys}")
                for name in all_keys:
                    if name not in self._states:
                        self._states[name] = MonitorState()

                    state = self._states[name]

                    if name in results:
                        result = results[name]
                        is_good = True
                        reason = "Condition check returned False"

                        if isinstance(result, bool):
                            is_good = result
                        elif isinstance(result, str):
                            is_good = False
                            reason = result
                        else:
                            is_good, reason = result
                    else:
                        is_good = False
                        reason = "Key missing from check results"

                    if is_good:
                        logger.debug(
                            f"[{self.name}] '{name}' is good, alerting={state.is_alerting}"
                        )
                        if state.is_alerting:
                            logger.info(
                                f"[{self.name}] Condition '{name}' returned to normal. Resolving alert."
                            )
                            try:
                                await self.resolve_alert(name)
                            except Exception:
                                logger.exception(
                                    f"[{self.name}] Error in resolve_alert for '{name}'",
                                )
                        elif state.first_failure_time is not None:
                            elapsed = now - state.first_failure_time
                            logger.info(
                                f"[{self.name}] Condition '{name}' returned to normal before alert triggered after {format_seconds(elapsed)}."
                            )

                        state.first_failure_time = None
                        state.last_alert_time = None
                        state.is_alerting = False
                    elif state.first_failure_time is None:
                        state.first_failure_time = now
                        logger.debug(f"[{self.name}] '{name}' first failure at {now}")
                    else:
                        elapsed = now - state.first_failure_time
                        logger.debug(
                            f"[{self.name}] '{name}' failure elapsed {elapsed:.2f}s"
                        )
                        if elapsed >= self.timeout:
                            if not state.is_alerting:
                                logger.warning(
                                    f"[{self.name}] Alert timer expired for '{name}'. Starting alerts."
                                )
                                state.is_alerting = True

                            if (
                                state.last_alert_time is None
                                or (now - state.last_alert_time) >= self.alert_interval
                            ):
                                try:
                                    logger.info(
                                        f"[{self.name}] Triggering alert for '{name}': {reason}"
                                    )
                                    await self.trigger_alert(name, reason)
                                    state.last_alert_time = now
                                except Exception:
                                    logger.exception(
                                        f"[{self.name}] Error in trigger_alert for '{name}'",
                                    )

                await asyncio.sleep(self.interval)

        except asyncio.CancelledError:
            logger.info(f"[{self.name}] BatchAlertMonitor loop cancelled.")
            raise
        except Exception:
            logger.critical(
                f"[{self.name}] BatchAlertMonitor loop crashed",
            )
            self._running = False
