"""Run with: python -m unittest tests.test_ravenfall_shutdown

Sandboxie and the game itself are faked. The restart tasks and RFChannel's restart
logic are the real code.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from bot.cogs.game import GameCog
from bot.models import RFChannelEvent
from bot.ravenfallrestarttask import PreRestartEvent, RestartReason
from tests.fastclock import speed_up_time
from tests.test_ravenfall_updater import FakeManager, make_town


class TownTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        speed_up_time(self)
        self.log: list[str] = []
        self.reports: list[str] = []
        self.manager = FakeManager()
        self.shutdown = self.manager.town_shutdown
        self.survivors: set[str] = set()

        async def stop_process(box, _process):
            self.log.append(f"stop {box}")
            if box not in self.survivors:
                self.town_named(box).fake_version = None

        async def start_process(box, _command):
            self.log.append(f"start {box}")
            self.town_named(box).fake_version = "1"
            return 0, ""

        async def restart_process(box, process, command):
            await stop_process(box, process)
            return await start_process(box, command)

        for target, replacement in (
            ("bot.ravenfallrestarttask.WARNING_MSG_TIMES", ((3, PreRestartEvent.WARNING), (2, PreRestartEvent.PRE_RESTART))),
            ("bot.ravenfallchannel.stop_process", stop_process),
            ("bot.ravenfallchannel.start_process", start_process),
            ("bot.ravenfallchannel.restart_process", restart_process),
        ):
            patcher = mock.patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.a = make_town(self.manager, "a", "1", self.log)
        self.b = make_town(self.manager, "b", "1", self.log)

    def town_named(self, name: str):
        return next(town for town in self.manager.channels if town.channel_name == name)

    async def report(self, message: str):
        self.reports.append(message)

    def stop(self, seconds: int = 3, force: bool = False, towns=None):
        self.assertTrue(self.shutdown.stop(towns or self.manager.channels, seconds, force, self.report))

    async def wait_for_runs(self):
        await asyncio.wait_for(asyncio.gather(*self.shutdown._run_tasks), 30)

    async def stop_and_wait(self, seconds: int = 3, force: bool = False, towns=None):
        self.stop(seconds, force, towns)
        await self.wait_for_runs()

    async def wait_for(self, condition):
        async with asyncio.timeout(30):
            while not condition():
                await asyncio.sleep(0.02)

    async def start_and_wait(self, towns=None):
        result = await self.shutdown.start(towns or self.manager.channels)
        await asyncio.wait_for(asyncio.gather(*(town.restart_task.wait() for town in result.starting)), 30)
        return result


class ShutdownTests(TownTestCase):
    async def test_stops_every_town_without_starting_them(self):
        await self.stop_and_wait()

        for name in ("a", "b"):
            self.assertLess(self.log.index(f"pre {name}"), self.log.index(f"stop {name}"))
        self.assertFalse([entry for entry in self.log if entry.startswith(("start", "post"))])
        self.assertEqual(self.reports, ["All towns have been shut down."])
        self.assertTrue(self.shutdown.active)
        self.assertFalse(self.a.channel_restart_lock.locked())

    async def test_restarts_are_held_while_shut_down(self):
        await self.stop_and_wait()

        self.assertIsNone(self.a.queue_restart(2, reason=RestartReason.UNRESPONSIVE))
        self.assertIsNone(self.a.queue_restart(2, reason=RestartReason.USER))
        await self.a.auto_restart_routine()
        self.assertTrue(self.a.restart_task.finished())

    async def test_shutting_down_twice_is_refused(self):
        await self.stop_and_wait()
        self.assertEqual(self.shutdown.stop(self.manager.channels, 1, False, self.report), [])

    async def test_start_brings_back_only_towns_that_are_down(self):
        await self.stop_and_wait()
        self.b.fake_version = "1"  # someone started it by hand
        self.log.clear()

        result = await self.start_and_wait()

        self.assertEqual(result.starting, [self.a])
        self.assertEqual(result.cancelled, [])
        self.assertIn("start a", self.log)
        self.assertIn("post a", self.log)
        self.assertNotIn("start b", self.log)
        self.assertFalse(self.shutdown.active)
        self.assertIsNotNone(self.a.queue_restart(60, reason=RestartReason.UNRESPONSIVE))
        self.a.restart_task.cancel()

    async def test_start_cancels_a_countdown_and_leaves_towns_alone(self):
        self.stop(seconds=25)
        await self.wait_for(lambda: self.a.restart_task and self.b.restart_task)

        result = await self.start_and_wait()

        self.assertEqual(result.starting, [])
        self.assertEqual(result.cancelled, [self.a, self.b])
        self.assertEqual(self.log, [])
        self.assertFalse(self.shutdown.active)
        self.assertEqual(self.reports, [])

    async def test_start_restarts_towns_whose_players_already_left(self):
        self.stop(force=True)
        await self.wait_for(lambda: "pre a" in self.log and "pre b" in self.log)

        result = await self.start_and_wait()

        self.assertCountEqual(result.starting, [self.a, self.b])
        self.assertEqual(result.cancelled, [])
        self.assertIn("post a", self.log)
        self.assertIn("post b", self.log)

    async def test_waits_for_a_dungeon(self):
        self.a.event = RFChannelEvent.DUNGEON
        self.a.dungeon = {"players": 3}
        self.stop()
        await self.wait_for(lambda: self.a.restart_task and self.a.restart_task.paused())
        await self.wait_for(lambda: "stop b" in self.log)
        self.assertNotIn("stop a", self.log)

        self.a.event = RFChannelEvent.NONE
        self.a.restart_task.time_to_restart = 0
        self.a.restart_task.unpause()
        await self.wait_for_runs()
        self.assertIn("stop a", self.log)

    async def test_force_ignores_a_dungeon(self):
        self.a.event = RFChannelEvent.DUNGEON
        self.a.dungeon = {"players": 3}
        await self.stop_and_wait(force=True)
        self.assertIn("stop a", self.log)

    async def test_a_server_outage_does_not_hold_up_the_shutdown(self):
        self.manager.ravennest_is_online = False
        await self.stop_and_wait()
        self.assertIn("stop a", self.log)
        self.assertIn("stop b", self.log)

    async def test_reports_towns_that_did_not_stop(self):
        self.survivors.add("b")
        await self.stop_and_wait()
        self.assertEqual(self.reports, ["These towns are still running: b"])

    async def test_works_with_monitoring_paused(self):
        self.a.monitoring_paused = True
        await self.stop_and_wait()
        self.assertIn("stop a", self.log)

    async def test_start_with_nothing_shut_down_does_nothing(self):
        result = await self.start_and_wait()
        self.assertEqual(result, ([], []))

    async def test_stops_one_town_and_leaves_the_others_alone(self):
        await self.stop_and_wait(towns=[self.a])

        self.assertIn("stop a", self.log)
        self.assertNotIn("stop b", self.log)
        self.assertEqual(self.reports, ["Shut down a."])
        self.assertEqual(self.shutdown.held, {self.a})
        self.assertIsNone(self.a.queue_restart(2, reason=RestartReason.USER))
        self.assertIsNotNone(self.b.queue_restart(60, reason=RestartReason.USER))
        self.b.restart_task.cancel()

    async def test_starts_one_town_and_keeps_the_other_held(self):
        await self.stop_and_wait()
        self.log.clear()

        result = await self.start_and_wait([self.a])

        self.assertEqual(result.starting, [self.a])
        self.assertIn("start a", self.log)
        self.assertNotIn("start b", self.log)
        self.assertEqual(self.shutdown.held, {self.b})

    async def test_cancelling_one_town_lets_the_other_go_down(self):
        self.stop()
        await self.wait_for(lambda: self.a.restart_task and self.b.restart_task)

        result = await self.start_and_wait([self.a])
        await self.wait_for_runs()

        self.assertEqual(result.cancelled, [self.a])
        self.assertNotIn("stop a", self.log)
        self.assertIn("stop b", self.log)
        self.assertEqual(self.reports, ["Shut down b."])

    async def test_cancelling_while_a_restart_finishes_queues_no_shutdown(self):
        entered = asyncio.Event()
        gate = asyncio.Event()

        async def finishing(_towns):
            entered.set()
            await gate.wait()

        with mock.patch.object(self.manager.game_updater, "finish_running_restarts", finishing):
            self.stop(towns=[self.a])
            await entered.wait()
            result = await self.start_and_wait([self.a])
            gate.set()
            await self.wait_for_runs()

        self.assertEqual(result.cancelled, [self.a])
        self.assertIsNone(self.a.restart_task)
        self.assertEqual(self.reports, [])

    async def test_towns_can_shut_down_at_different_times(self):
        self.stop(seconds=25, towns=[self.a])
        await self.wait_for(lambda: self.a.restart_task)
        self.stop(towns=[self.b])
        await self.wait_for(lambda: self.reports)

        self.assertEqual(self.reports, ["Shut down b."])
        self.assertEqual(self.shutdown.held, {self.a, self.b})
        await self.start_and_wait([self.a])


class CommandTests(TownTestCase):
    """Runs the commands themselves, without the framework's argument parsing."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.cog = GameCog.__new__(GameCog)
        self.cog.global_context = SimpleNamespace(ravenfall_manager=self.manager)
        self.replies: list[str] = []

        async def reply(message: str):
            self.replies.append(message)

        self.ctx = SimpleNamespace(message=SimpleNamespace(reply=reply))

    async def test_rfshutdown_cancel_says_the_countdown_was_cancelled(self):
        await self.cog.rfshutdown(self.ctx, 25, channel=self.a)
        await self.wait_for(lambda: self.a.restart_task)
        self.assertEqual(self.shutdown.held, {self.a})
        await self.cog.rfshutdowncancel(self.ctx, channel=self.a)

        self.assertEqual(self.replies, [
            "Shutting down a in 25s. Use rfshutdown cancel to cancel.",
            "Shutdown cancelled for a.",
        ])
        self.assertEqual(self.shutdown.held, set())

    async def test_rfshutdown_all_flag(self):
        await self.cog.rfshutdown(self.ctx, 25, channel=self.a, all_=True)
        self.assertEqual(self.shutdown.held, {self.a, self.b})
        await self.wait_for(lambda: self.a.restart_task and self.b.restart_task)
        await self.cog.rfshutdowncancel(self.ctx, channel=self.a, all_=True)

        self.assertEqual(self.replies, [
            "Shutting down all towns in 25s. Use rfshutdown cancel --all to cancel.",
            "Shutdown cancelled for a, b.",
        ])

    async def test_rfstopall_covers_every_town(self):
        await self.cog.rfstopall(self.ctx, 25)
        self.assertEqual(self.shutdown.held, {self.a, self.b})
        await self.wait_for(lambda: self.a.restart_task and self.b.restart_task)
        await self.cog.rfstartall(self.ctx)
        self.assertEqual(self.replies[-1], "Shutdown cancelled for a, b.")

    async def test_rfshutdown_cancel_leaves_a_town_that_is_down(self):
        await self.cog.rfshutdown(self.ctx, 3, channel=self.a)
        await self.wait_for_runs()
        await self.cog.rfshutdowncancel(self.ctx, channel=self.a)

        self.assertEqual(self.replies[-1], "There is no shutdown to cancel.")
        self.assertEqual(self.shutdown.held, {self.a})
        self.assertNotIn("start a", self.log)

    async def test_rfstart_brings_back_a_town_that_is_down(self):
        await self.stop_and_wait(towns=[self.a])
        await self.cog.rfstart(self.ctx, channel=self.a)
        await asyncio.wait_for(self.a.restart_task.wait(), 30)

        self.assertEqual(self.replies, ["Starting a..."])
        self.assertIn("start a", self.log)

    async def test_rfstart_with_nothing_to_do(self):
        await self.cog.rfstart(self.ctx, channel=self.a)
        self.assertEqual(self.replies, ["There are no towns to start."])

    async def test_rfrestart_defaults_to_one_town(self):
        await self.cog.rfrestart(self.ctx, 60, channel=self.a)
        self.assertIsNotNone(self.a.restart_task)
        self.assertIsNone(self.b.restart_task)
        self.assertEqual(self.replies, ["Restart queued. Restarting in 60s."])
        self.a.restart_task.cancel()

    async def test_rfrestart_all_skips_shut_down_towns(self):
        self.shutdown.held.add(self.a)
        await self.cog.rfrestart(self.ctx, 60, channel=self.a, all_=True)
        self.assertIsNone(self.a.restart_task)
        self.assertEqual(self.replies, ["Restart queued for b. Restarting in 60s."])
        self.b.restart_task.cancel()

    async def test_rfrestartcancel_points_at_the_right_commands(self):
        self.shutdown.held.add(self.a)
        await self.cog.rfrestartcancel(self.ctx, channel=self.a)
        self.assertEqual(self.replies, ["a is shut down, use rfshutdown cancel or rfstart instead."])
