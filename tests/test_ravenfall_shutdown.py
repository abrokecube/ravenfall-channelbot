"""Run with: python -m unittest tests.test_ravenfall_shutdown

Sandboxie and the game itself are faked. The restart tasks and RFChannel's restart
logic are the real code.
"""

import asyncio
import unittest
from unittest import mock

from bot.models import RFChannelEvent
from bot.ravenfallrestarttask import PreRestartEvent, RestartReason
from tests.test_ravenfall_updater import FakeManager, make_town


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
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

    def stop(self, seconds: int = 3, force: bool = False):
        self.assertTrue(self.shutdown.stop(seconds, force, self.report))

    async def stop_and_wait(self, seconds: int = 3, force: bool = False):
        self.stop(seconds, force)
        await asyncio.wait_for(self.shutdown._run_task, 30)

    async def wait_for(self, condition):
        async with asyncio.timeout(30):
            while not condition():
                await asyncio.sleep(0.02)

    async def start_and_wait(self):
        towns = await self.shutdown.start()
        await asyncio.wait_for(asyncio.gather(*(town.restart_task.wait() for town in towns)), 30)
        return towns

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
        self.assertFalse(self.shutdown.stop(1, False, self.report))

    async def test_start_brings_back_only_towns_that_are_down(self):
        await self.stop_and_wait()
        self.b.fake_version = "1"  # someone started it by hand
        self.log.clear()

        towns = await self.start_and_wait()

        self.assertEqual(towns, [self.a])
        self.assertIn("start a", self.log)
        self.assertIn("post a", self.log)
        self.assertNotIn("start b", self.log)
        self.assertFalse(self.shutdown.active)
        self.assertIsNotNone(self.a.queue_restart(60, reason=RestartReason.UNRESPONSIVE))
        self.a.restart_task.cancel()

    async def test_start_cancels_a_countdown_and_leaves_towns_alone(self):
        self.stop(seconds=25)
        await self.wait_for(lambda: self.a.restart_task and self.b.restart_task)

        towns = await self.start_and_wait()

        self.assertEqual(towns, [])
        self.assertEqual(self.log, [])
        self.assertFalse(self.shutdown.active)
        self.assertEqual(self.reports, [])

    async def test_start_restarts_towns_whose_players_already_left(self):
        self.stop(force=True)
        await self.wait_for(lambda: "pre a" in self.log and "pre b" in self.log)

        towns = await self.start_and_wait()

        self.assertCountEqual(towns, [self.a, self.b])
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
        await asyncio.wait_for(self.shutdown._run_task, 30)
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
