"""Run with: python -m unittest tests.test_ravenfall_updater

Sandboxie and the game itself are faked. Everything else is the real code: the
restart tasks, RFChannel's restart logic, and the file handling on temp folders.
"""

import asyncio
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

os.environ.setdefault("MULTICHAT_COMMAND_SERVER_HOST", "127.0.0.1")
os.environ.setdefault("MULTICHAT_COMMAND_SERVER_PORT", "7200")
os.environ.setdefault("RF_MIDDLEMAN_HOST", "127.0.0.1")
os.environ.setdefault("RF_MIDDLEMAN_PORT", "7101")

from aiohttp import web

from bot import ravenfallupdater as updater
from bot.models import RFChannelEvent, RFChannelSubEvent
from bot.ravenfallchannel import RFChannel
from bot.ravenfallrestarttask import RestartReason
from bot.ravenfallupdater import GameRelease, RFGameUpdater, TownGate


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class VersionTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(updater.normalize_version("v0.9.6.0a-alpha"), "0.9.6.0a")
        self.assertEqual(updater.normalize_version(" 0.9.6.0A "), "0.9.6.0a")

    def test_is_newer_than(self):
        release = GameRelease("0.9.6.0a", "", "")
        self.assertTrue(release.is_newer_than({"a": "0.9.4.3a"}))
        self.assertTrue(release.is_newer_than({"a": "0.9.6.0a", "b": "0.9.4.3a"}))
        self.assertFalse(release.is_newer_than({"a": "0.9.6.0a", "b": None}))
        self.assertIsNone(release.is_newer_than({"a": None, "b": None}))


class FileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / "source"
        self.game = self.root / "game"
        self.box = self.root / "box"
        write(self.source / "Ravenfall.exe", "new")
        write(self.source / "Ravenfall_Data" / "level0", "new")
        write(self.source / "updater" / "RavenWeave.exe", "new")
        write(self.source / "updater" / "SharpCompress.dll", "new")

    def test_install(self):
        write(self.game / "Ravenfall.exe", "old")
        write(self.game / "RavenWeave.exe", "old")
        write(self.game / "updater" / "RavenWeave.exe", "old")
        write(self.game / "state-data.json", "mine")
        write(self.box / "RavenWeave.exe", "old")
        write(self.box / "state-data.json", "mine")

        count = updater.install_files(self.source, self.game, [self.box, self.root / "empty box"])

        self.assertEqual(count, 2)
        self.assertEqual((self.game / "Ravenfall.exe").read_text(), "new")
        self.assertEqual((self.game / "Ravenfall_Data" / "level0").read_text(), "new")
        self.assertEqual((self.game / "state-data.json").read_text(), "mine")
        self.assertEqual((self.box / "state-data.json").read_text(), "mine")
        # The game moves updater\ into place on startup, so it must never be left there
        self.assertFalse((self.game / "updater").exists())
        self.assertFalse((self.game / "RavenWeave.exe").exists())
        self.assertFalse((self.box / "RavenWeave.exe").exists())

    def test_read_only_files_are_not_locked(self):
        target = write(self.game / "Ravenfall.exe", "old")
        target.chmod(stat.S_IREAD)
        self.assertEqual(updater.find_locked_files(self.source, self.game), [])
        updater.install_files(self.source, self.game)
        self.assertEqual(target.read_text(), "new")

    @unittest.skipUnless(os.name == "nt", "relies on Windows locking running executables")
    def test_running_executable_is_locked(self):
        running = self.game / "Ravenfall.exe"
        running.parent.mkdir()
        shutil.copy(r"C:\Windows\System32\ping.exe", running)
        process = subprocess.Popen([str(running), "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL)
        try:
            self.assertEqual(updater.find_locked_files(self.source, self.game), [Path("Ravenfall.exe")])
        finally:
            process.kill()
            process.wait()
        self.assertEqual(updater.find_locked_files(self.source, self.game), [])


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.body = b"update" * 1000
        app = web.Application()
        app.router.add_get("/update.7z", lambda _: web.Response(body=self.body))
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/update.7z"
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.destination = Path(folder.name) / "update.7z"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_good_checksum(self):
        sha256 = hashlib.sha256(self.body).hexdigest()
        await updater.download_release(GameRelease("1", self.url, sha256), self.destination)
        self.assertEqual(self.destination.read_bytes(), self.body)

    async def test_bad_checksum(self):
        with self.assertRaises(updater.GameUpdateError):
            await updater.download_release(GameRelease("1", self.url, "0" * 64), self.destination)


class FetchReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_download_from_elsewhere(self):
        class Response:
            status = 200
            def raise_for_status(self): pass
            async def json(self): return {"version": "1", "downloadUrl": "https://evil.example/update.7z"}
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass

        with mock.patch("aiohttp.ClientSession.get", return_value=Response()):
            with self.assertRaises(updater.GameUpdateError):
                await updater.fetch_latest_release()


class FakeManager:
    def __init__(self):
        self.channels = []
        self.global_restart_lock = asyncio.Lock()
        self.ravennest_is_online = True
        self.middleman_power_saving = False
        self.middleman_connected = False
        self.game_updater = RFGameUpdater(self)

    async def check_update_endpoint(self):
        return True


def make_town(manager: FakeManager, name: str, version: str, log: list, auth_failures: int = 0) -> RFChannel:
    town = RFChannel.__new__(RFChannel)
    town.manager = manager
    town.channel_name = name
    town.channel_id = name
    town.sandboxie_box = name
    town.ravenfall_start_script = "start.bat"
    town.restart_timeout = 1
    town.monitoring_paused = False
    town.restart_task = None
    town.ravenfall_restart_attempts = 0
    town.global_restart_lock = manager.global_restart_lock
    town.channel_restart_lock = asyncio.Lock()
    town.channel_post_restart_lock = asyncio.Lock()
    town.sub_event = RFChannelSubEvent.NONE
    town.event = RFChannelEvent.NONE
    town.dungeon = {"players": 0}
    town.raid = {"players": 0}
    town.fake_version = version
    town.fake_auth_failures = auth_failures

    async def get_query(*_args, **_kwargs):
        if town.fake_auth_failures > 0:
            return {"authenticated": False}
        return {"authenticated": True, "players": 1, "gameversion": town.fake_version}

    async def noop(*_args, **_kwargs):
        pass

    async def pre_restart():
        log.append(f"pre {name}")

    async def post_restart():
        log.append(f"post {name}")

    town.get_query = get_query
    town.send_chat_message = noop
    town.send_announcement = noop
    town._ravenfall_pre_restart = pre_restart
    town._ravenfall_post_restart = post_restart
    manager.channels.append(town)
    return town


class UpdateFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.log: list[str] = []
        self.manager = FakeManager()
        self.game_updater = self.manager.game_updater
        self.reports: list[str] = []
        self.release = GameRelease("0.9.6.0a", "https://example.invalid", "")

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.game = Path(folder.name) / "game"
        write(self.game / "Ravenfall.exe", "old")
        write(self.game / "RavenWeave.exe", "old")
        env = mock.patch.dict(os.environ, {
            "RAVENFALL_FOLDER": str(self.game),
            "RAVENFALL_SANDBOXED_FOLDER": str(Path(folder.name) / "{box}"),
        })
        env.start()
        self.addCleanup(env.stop)

        log = self.log

        async def stop_process(box, _process):
            log.append(f"stop {box}")

        async def start_process(box, _command):
            log.append(f"start {box}")
            self.town_named(box).fake_version = self.new_version
            return 0, ""

        async def restart_process(box, process, command):
            await stop_process(box, process)
            return await start_process(box, command)

        async def download(_release, destination):
            log.append("download")
            destination.write_bytes(b"")

        async def extract(_archive, destination):
            log.append("extract")
            write(destination / "Ravenfall.exe", "new")
            write(destination / "Ravenfall_Data" / "level0", "new")
            write(destination / "updater" / "RavenWeave.exe", "new")

        real_install = updater.install_files

        def install(*args):
            log.append("install")
            return real_install(*args)

        self.new_version = "0.9.6.0a"
        for constant, value in (("EVENTS_SETTLE_TIME", 0.1), ("EVENT_POLL_INTERVAL", 0.05)):
            patcher = mock.patch.object(updater, constant, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, replacement in (
            ("bot.ravenfallchannel.stop_process", stop_process),
            ("bot.ravenfallchannel.start_process", start_process),
            ("bot.ravenfallchannel.restart_process", restart_process),
            ("bot.ravenfallupdater.download_release", download),
            ("bot.ravenfallupdater.extract_archive", extract),
            ("bot.ravenfallupdater.install_files", install),
        ):
            patcher = mock.patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def town_named(self, name: str) -> RFChannel:
        return next(town for town in self.manager.channels if town.channel_name == name)

    async def report(self, message: str):
        self.reports.append(message)

    async def run_update(self, seconds: int = 2):
        self.assertTrue(self.game_updater.start(self.release, seconds, self.report))
        await asyncio.wait_for(self.game_updater._run_task, 60)

    async def test_all_towns_stop_before_install_then_all_start(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)
        make_town(self.manager, "b", "0.9.4.3a", self.log)
        await self.run_update()

        self.assertEqual(self.log.count("install"), 1)
        install = self.log.index("install")
        self.assertLess(self.log.index("download"), self.log.index("stop a"))
        self.assertLess(self.log.index("extract"), self.log.index("stop a"))
        self.assertLess(self.log.index("stop a"), install)
        self.assertLess(self.log.index("stop b"), install)
        self.assertLess(install, self.log.index("start a"))
        self.assertLess(install, self.log.index("start b"))
        self.assertEqual(self.log.count("stop a"), 1)
        self.assertIn("post a", self.log)
        self.assertIn("post b", self.log)

        self.assertEqual((self.game / "Ravenfall.exe").read_text(), "new")
        self.assertFalse((self.game / "RavenWeave.exe").exists())
        self.assertFalse((self.game / "updater").exists())
        self.assertIn("Ravenfall has been updated to 0.9.6.0a!", self.reports)
        self.assertFalse(self.game_updater.in_progress)
        self.assertFalse(self.game_updater.holding_restarts)
        for town in self.manager.channels:
            self.assertFalse(town.channel_restart_lock.locked())
            self.assertFalse(town.channel_post_restart_lock.locked())
        self.assertFalse(self.manager.global_restart_lock.locked())

    async def test_other_restarts_are_refused_and_pending_ones_replaced(self):
        town = make_town(self.manager, "a", "0.9.4.3a", self.log)
        pending = town.queue_restart(300, label="Scheduled restart", reason=RestartReason.AUTO)
        self.assertTrue(self.game_updater.start(self.release, 2, self.report))
        while not self.game_updater.holding_restarts:
            await asyncio.sleep(0.01)
        self.assertIsNone(town.queue_restart(2, reason=RestartReason.UNRESPONSIVE))
        await asyncio.wait_for(self.game_updater._run_task, 60)
        self.assertTrue(pending.finished())
        self.assertEqual(self.log.count("stop a"), 1)
        self.assertIsNotNone(town.queue_restart(300, reason=RestartReason.AUTO))
        town.restart_task.cancel()

    async def test_countdown_waits_for_every_dungeon_and_raid(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)
        busy = make_town(self.manager, "b", "0.9.4.3a", self.log)
        busy.event = RFChannelEvent.RAID

        self.assertTrue(self.game_updater.start(self.release, 1, self.report))
        await asyncio.sleep(1)
        self.assertEqual(self.game_updater._restart_tasks, [])
        self.assertIsNone(self.town_named("a").restart_task)
        self.assertNotIn("stop a", self.log)
        self.assertEqual(self.game_updater.status, "waiting for dungeons and raids to finish")
        self.assertEqual(len(self.reports), 1)
        self.assertIn("waiting for dungeons and raids", self.reports[0])

        busy.event = RFChannelEvent.NONE
        await asyncio.wait_for(self.game_updater._run_task, 60)
        self.assertEqual(self.log.count("install"), 1)

    async def test_nobody_stops_while_a_late_event_is_running(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)
        late = make_town(self.manager, "b", "0.9.4.3a", self.log)

        self.assertTrue(self.game_updater.start(self.release, 3, self.report))
        while not self.game_updater._restart_tasks:
            await asyncio.sleep(0.01)
        # A dungeon pops up on its own during the countdown, with no one in it
        late.event = RFChannelEvent.DUNGEON
        await asyncio.sleep(5)
        self.assertLessEqual(self.town_named("a").restart_task.get_time_left(), 0)
        self.assertNotIn("stop a", self.log)
        self.assertNotIn("stop b", self.log)

        late.event = RFChannelEvent.NONE
        await asyncio.wait_for(self.game_updater._run_task, 60)
        self.assertEqual(self.log.count("install"), 1)
        first_start = min(self.log.index("start a"), self.log.index("start b"))
        self.assertLess(self.log.index("stop a"), first_start)
        self.assertLess(self.log.index("stop b"), first_start)

    async def test_paused_town_holds_up_the_others(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)
        held = make_town(self.manager, "b", "0.9.4.3a", self.log)

        self.assertTrue(self.game_updater.start(self.release, 4, self.report))
        while not self.game_updater._restart_tasks:
            await asyncio.sleep(0.01)
        held.sub_event = RFChannelSubEvent.DUNGEON_PREPARE
        await asyncio.sleep(6)
        self.assertEqual(self.town_named("a").restart_task.pause_event_name, "waiting for another town")
        self.assertNotIn("stop a", self.log)
        self.assertTrue(self.game_updater.cancel())
        await asyncio.wait_for(self.game_updater._run_task, 10)
        self.assertNotIn("stop a", self.log)
        self.assertNotIn("stop b", self.log)

    async def test_scrolls_are_paused_for_the_whole_update_and_resume_after(self):
        town = make_town(self.manager, "a", "0.9.4.3a", self.log)
        town.update_events_routine_first_iteration = False
        town.scroll_queue = deque([object()])
        town.scroll_queue_failures = 0
        town.restart_task = None
        calls = []

        async def get_scroll_counts(_channel_id):
            calls.append(self.game_updater.holding_scrolls)
            raise asyncio.CancelledError  # reaching this means a scroll was about to be used

        async def try_scroll():
            try:
                await RFChannel.scroll_queue_routine._coro(town)
            except asyncio.CancelledError:
                pass

        with mock.patch("bot.ravenfallchannel.get_scroll_counts", get_scroll_counts):
            self.assertTrue(self.game_updater.start(self.release, 1, self.report))
            await try_scroll()  # as soon as the update starts
            while "stop a" not in self.log:
                await try_scroll()
                await asyncio.sleep(0.01)
            while "post a" not in self.log:
                await try_scroll()  # also after the game is back, until the update is over
                await asyncio.sleep(0.01)
            self.assertEqual(calls, [])

            await asyncio.wait_for(self.game_updater._run_task, 60)
            self.assertFalse(self.game_updater.holding_scrolls)
            await try_scroll()
            self.assertEqual(calls, [False])

    async def test_scrolls_resume_after_a_failed_or_cancelled_update(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)

        async def fail(*_):
            raise updater.GameUpdateError("no internet")

        with mock.patch("bot.ravenfallupdater.download_release", fail):
            await self.run_update()
        self.assertFalse(self.game_updater.holding_scrolls)

        self.town_named("a").event = RFChannelEvent.DUNGEON
        self.assertTrue(self.game_updater.start(self.release, 1, self.report))
        await asyncio.sleep(0.1)
        self.assertTrue(self.game_updater.holding_scrolls)
        self.assertTrue(self.game_updater.cancel())
        await asyncio.wait_for(self.game_updater._run_task, 10)
        self.assertFalse(self.game_updater.holding_scrolls)

    async def test_gives_up_on_a_town_that_never_stops(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)
        gate = TownGate(mock.AsyncMock())
        gate.tasks = [object(), object()]
        with mock.patch.object(updater, "TOWNS_STOPPED_TIMEOUT", 0.05):
            await gate.town_stopped()
        self.assertTrue(gate.abandoned)
        gate.install.assert_not_called()

    async def test_failed_install_still_restarts_the_towns(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)
        make_town(self.manager, "b", "0.9.4.3a", self.log)
        self.new_version = "0.9.4.3a"
        with mock.patch("bot.ravenfallupdater.install_files", side_effect=OSError("disk full")):
            await self.run_update()
        self.assertIn("start a", self.log)
        self.assertIn("start b", self.log)
        self.assertEqual(len(self.reports), 1)
        self.assertIn("disk full", self.reports[0])

    async def test_failed_download_touches_nothing(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)

        async def fail(*_):
            raise updater.GameUpdateError("no internet")

        with mock.patch("bot.ravenfallupdater.download_release", fail):
            await self.run_update()
        self.assertEqual(self.log, [])
        self.assertIn("no internet", self.reports[0])
        self.assertFalse(self.game_updater.holding_restarts)

    async def test_town_left_behind_is_reported(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)
        self.new_version = "0.9.4.3a"
        await self.run_update()
        self.assertIn("a (0.9.4.3a)", self.reports[0])

    async def test_cancel_during_countdown(self):
        town = make_town(self.manager, "a", "0.9.4.3a", self.log)
        self.assertTrue(self.game_updater.start(self.release, 300, self.report))
        while not self.game_updater._restart_tasks:
            await asyncio.sleep(0.01)
        self.assertTrue(self.game_updater.cancel())
        await asyncio.wait_for(self.game_updater._run_task, 10)
        self.assertFalse(self.game_updater.holding_restarts)
        self.assertTrue(town.restart_task.finished())
        self.assertNotIn("stop a", self.log)

    async def test_cannot_cancel_once_a_town_is_going_down(self):
        make_town(self.manager, "a", "0.9.4.3a", self.log)
        self.assertTrue(self.game_updater.start(self.release, 1, self.report))
        while "stop a" not in self.log:
            await asyncio.sleep(0.01)
        self.assertFalse(self.game_updater.cancel())
        await asyncio.wait_for(self.game_updater._run_task, 60)

    async def test_restart_is_retried_when_the_game_does_not_log_in(self):
        town = make_town(self.manager, "a", "0.9.4.3a", self.log, auth_failures=1)
        real_sleep = asyncio.sleep

        async def fast_sleep(seconds):
            town.fake_auth_failures = 0 if seconds == 20 else town.fake_auth_failures
            await real_sleep(min(seconds, 0.01))

        with mock.patch("bot.ravenfallchannel.asyncio.sleep", fast_sleep):
            self.assertTrue(await town._restart_ravenfall(run_pre_restart=False))
        self.assertEqual(self.log.count("start a"), 2)
        self.assertIn("post a", self.log)
        self.assertFalse(town.channel_restart_lock.locked())

    async def test_cancelling_mid_restart_releases_the_locks(self):
        town = make_town(self.manager, "a", "0.9.4.3a", self.log, auth_failures=99)
        town.restart_timeout = 30
        task = town.queue_restart(0, reason=RestartReason.USER)
        while not town.channel_restart_lock.locked():
            await asyncio.sleep(0.01)
        self.assertTrue(town.cancel_restart())
        await task.wait()
        self.assertFalse(town.channel_restart_lock.locked())
        self.assertFalse(self.manager.global_restart_lock.locked())


if __name__ == "__main__":
    unittest.main()
