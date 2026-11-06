"""Updates the shared Ravenfall install from the game's GitHub releases.

The towns all run out of one folder through Sandboxie, so they have to go down
together: nothing can be overwritten while any of them still has the files open.
The update is downloaded and unpacked first, so a bad download never costs any
downtime. Towns download new versions by themselves in the background, each into
an update\\ folder of its own because of the sandbox, so one of those is used
instead when it is complete and matches the release. Scroll uses are paused for
the whole update, and nothing else happens until no town has a dungeon or raid
going. Each town then goes through its normal restart task (countdown, pre and post restart), with two additions: when its
countdown ends it waits for every other town to be ready, so that no town goes
down while another is held up by an event that started late, and after stopping
they wait for each other again, and the last one to stop installs the files.

RavenBot comes from the same release. While the towns are down it is stopped in
every box, its exe is replaced, and it is started again before the towns are, so
they find it running. Only the exe is touched, which leaves its settings alone.

RavenWeave, the game's own updater, is removed on purpose. Note that the update
carries it in updater\\, and that the game moves anything it finds there into its
own folder on startup, so that folder must not be installed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from string import ascii_lowercase
from typing import TYPE_CHECKING, Awaitable, Callable, Iterable, Iterator

import aiohttp

from .clients.session import get_session
from .models import RFChannelEvent
from .ravenfallrestarttask import RestartReason, RestartStatus, RFRestartTask

if TYPE_CHECKING:
    from .ravenfallchannel import RFChannel
    from .ravenfallmanager import RFChannelManager

logger = logging.getLogger(__name__)

VERSION_URL = "https://www.ravenfall.stream/api/version/check"
GITHUB_REPO = "zerratar/Ravenfall-Legacy"
GAME_ASSET = "update.7z"
RAVENBOT_ASSET = "RavenBot.7z"
DOWNLOAD_URL_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"

GAME_EXE = "Ravenfall.exe"
RAVENBOT_EXE = "RavenBot.exe"
UPDATER_EXE = "RavenWeave.exe"
UPDATER_FOLDER = "updater"
# Where the game keeps a background download. The ready file only exists once it is complete.
DOWNLOAD_FOLDER = "update"
DOWNLOAD_READY_FILE = "ready.json"

# How long a restart already underway gets to finish before it is cut off
RUNNING_RESTART_TIMEOUT = 5 * 60
# Once one town is down, how long it waits for the rest before giving up on the install
TOWNS_STOPPED_TIMEOUT = 5 * 60
# How long to wait for the stopped games to let go of their files
FILES_RELEASED_TIMEOUT = 30
# How long all towns must stay free of events before the countdown starts. This
# is longer than a scroll that was already on its way takes to show up as an event.
EVENTS_SETTLE_TIME = 15
EVENT_POLL_INTERVAL = 1


class GameUpdateError(Exception):
    pass


Report = Callable[[str], Awaitable[object]]


def normalize_version(version: str) -> str:
    """Reduces "v0.9.6.0a-alpha" and "0.9.6.0a" to "0.9.6.0", which is how towns report it."""
    return version.strip().lower().removeprefix("v").split("-")[0].rstrip(ascii_lowercase)


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    sha256: str


@dataclass(frozen=True)
class GameRelease:
    version: str
    game: ReleaseAsset
    ravenbot: ReleaseAsset

    def is_newer_than(self, installed: dict[str, str | None]) -> bool | None:
        """Whether any town is behind this release, or None if no town could say."""
        known = [version for version in installed.values() if version]
        if not known:
            return None
        return any(normalize_version(version) != normalize_version(self.version) for version in known)


async def fetch_latest_release() -> GameRelease:
    """Looks up the release that game clients are being told to install."""
    session = get_session()
    timeout = aiohttp.ClientTimeout(total=20)
    async with session.get(VERSION_URL, timeout=timeout) as response:
        response.raise_for_status()
        latest = await response.json()

    url: str = latest["downloadUrl"]
    if not url.startswith(DOWNLOAD_URL_PREFIX) or not url.endswith(f"/{GAME_ASSET}"):
        raise GameUpdateError(f"Unexpected download url: {url}")
    tag = url.split("/")[-2]

    release_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
    headers = {"Accept": "application/vnd.github+json"}
    async with session.get(release_url, headers=headers, timeout=timeout) as response:
        response.raise_for_status()
        release = await response.json()

    return GameRelease(
        version=latest["version"],
        game=find_asset(release, tag, GAME_ASSET),
        ravenbot=find_asset(release, tag, RAVENBOT_ASSET),
    )


def find_asset(release: dict, tag: str, name: str) -> ReleaseAsset:
    asset = next((a for a in release["assets"] if a["name"] == name), None)
    if asset is None:
        raise GameUpdateError(f"Release {tag} has no {name}")
    algorithm, _, digest = (asset.get("digest") or "").partition(":")
    if algorithm != "sha256" or not digest:
        raise GameUpdateError(f"GitHub did not provide a checksum for {name}")
    return ReleaseAsset(name, f"{DOWNLOAD_URL_PREFIX}{tag}/{name}", digest)


async def download_asset(asset: ReleaseAsset, destination: Path) -> None:
    timeout = aiohttp.ClientTimeout(total=None, sock_read=60)
    checksum = hashlib.sha256()
    async with get_session().get(asset.url, timeout=timeout) as response:
        response.raise_for_status()
        with destination.open("wb") as file:
            async for chunk in response.content.iter_chunked(1024 * 1024):
                checksum.update(chunk)
                await asyncio.to_thread(file.write, chunk)
    if checksum.hexdigest() != asset.sha256:
        raise GameUpdateError(f"Checksum mismatch on {asset.name}, the download is corrupt")


def copy_predownloaded_update(release: GameRelease, folders: Iterable[Path], destination: Path) -> bool:
    """Copies the first of the towns' background downloads that matches the release.

    It is copied, so that a town that is still running can't change it mid-unpack.
    Returns False if none of them can be used.
    """
    for folder in folders:
        archive = folder / DOWNLOAD_FOLDER / GAME_ASSET
        try:
            # The game writes the ready file itself, so it may come with a BOM
            ready = json.loads((folder / DOWNLOAD_FOLDER / DOWNLOAD_READY_FILE).read_text("utf-8-sig"))
            if ready.get("Version") != release.version or ready.get("Bytes") != archive.stat().st_size:
                continue
            shutil.copyfile(archive, destination)
            with destination.open("rb") as file:
                checksum = hashlib.file_digest(file, "sha256").hexdigest()
        except (OSError, ValueError, AttributeError) as e:
            logger.debug(f"Can't use the download in {folder}: {e}")
            continue
        if checksum == release.game.sha256:
            logger.info(f"Using the update that was already downloaded to {archive}")
            return True
        logger.warning(f"Ignoring {archive}, its checksum does not match the release")
    return False


def find_seven_zip() -> str:
    # The releases use the BCJ2 filter, which py7zr can't read
    candidates = (
        os.getenv("SEVEN_ZIP_PATH"),
        r"C:\Program Files\7-Zip\7z.exe",
        shutil.which("7z"),
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise GameUpdateError("7-Zip was not found, install it or set SEVEN_ZIP_PATH")


async def extract_archive(archive: Path, destination: Path, expected: Iterable[str]) -> None:
    """Unpacks the archive, and checks that the expected paths came out of it."""
    process = await asyncio.create_subprocess_exec(
        find_seven_zip(), "x", "-y", f"-o{destination}", str(archive),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
    if process.returncode != 0:
        raise GameUpdateError(f"7-Zip failed: {stderr.decode(errors='replace').strip()}")
    missing = [path for path in expected if not (destination / path).exists()]
    if missing:
        raise GameUpdateError(f"{archive.name} is missing {missing[0]}")


def iter_update_files(source: Path) -> Iterator[Path]:
    """Yields the paths, relative to source, of the files to install."""
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if relative.parts[0].lower() == UPDATER_FOLDER:
            continue
        if path.is_file() and not path.is_symlink():
            yield relative


def find_locked_files(source: Path, game_folder: Path) -> list[Path]:
    """Finds the files the update would replace that can't be written to."""
    locked = []
    for relative in iter_update_files(source):
        target = game_folder / relative
        if not target.exists():
            continue
        try:
            target.chmod(stat.S_IREAD | stat.S_IWRITE)  # clears the read-only flag
            with target.open("r+b"):
                pass
        except OSError:
            locked.append(relative)
    return locked


def install_files(source: Path, game_folder: Path, box_folders: Iterable[Path] = ()) -> int:
    """Copies the update over the game folder and removes the updater.

    box_folders are the sandboxed views of the game folder, which take priority
    over it, so an updater that ended up in one of them needs removing as well.
    Returns the number of files installed.
    """
    count = 0
    for relative in iter_update_files(source):
        target = game_folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
        count += 1
    for folder in (game_folder, *box_folders):
        (folder / UPDATER_EXE).unlink(missing_ok=True)
        shutil.rmtree(folder / UPDATER_FOLDER, ignore_errors=True)
    return count


def install_ravenbot(source: Path, ravenbot_folder: Path) -> None:
    shutil.copy2(source / RAVENBOT_EXE, ravenbot_folder / RAVENBOT_EXE)


async def wait_for_unlocked_files(source: Path, target_folder: Path) -> None:
    """Gives processes on their way out, like the crash handler, time to exit."""
    deadline = time.monotonic() + FILES_RELEASED_TIMEOUT
    while True:
        locked = await asyncio.to_thread(find_locked_files, source, target_folder)
        if not locked:
            return
        if time.monotonic() >= deadline:
            raise GameUpdateError(f"{len(locked)} files are still in use, e.g. {locked[0]}")
        await asyncio.sleep(2)


def has_event(channel: RFChannel) -> bool:
    return channel.event != RFChannelEvent.NONE


class TownGate:
    """Coordinates the towns around stopping, which they must all do together.

    Towns wait in town_ready until all of them can go down at once, and in
    town_stopped until all of them are down and the install has run.
    """

    def __init__(self, install: Callable[[], Awaitable[None]]):
        self.install = install
        self.tasks: list[RFRestartTask] = []
        self.stopped = 0
        self.abandoned = False
        self._go = asyncio.Event()
        self._all_stopped = asyncio.Event()
        self._finished = asyncio.Event()

    def _all_ready(self) -> bool:
        return all(
            task.get_time_left() <= 0 and not task.paused() and not has_event(task.channel)
            for task in self.tasks
        )

    async def town_ready(self) -> None:
        while not self._go.is_set():
            if self._all_ready():
                self._go.set()
                return
            try:
                await asyncio.wait_for(self._go.wait(), EVENT_POLL_INTERVAL)
            except TimeoutError:
                pass

    async def town_stopped(self) -> None:
        self.stopped += 1

        if self.stopped < len(self.tasks):
            try:
                await asyncio.wait_for(self._all_stopped.wait(), TOWNS_STOPPED_TIMEOUT)
            except TimeoutError:
                logger.error("Gave up waiting for all towns to stop, not installing the update")
                self.abandoned = True
                self._finished.set()
            await self._finished.wait()
            return

        if self.abandoned:
            return
        self._all_stopped.set()
        try:
            await self.install()
        finally:
            self._finished.set()


class RFGameUpdater:
    def __init__(self, manager: RFChannelManager):
        self.manager = manager
        self.holding_restarts = False
        self.holding_scrolls = False
        self.status = "idle"
        self._run_task: asyncio.Task | None = None
        self._restart_tasks: list[RFRestartTask] = []

    @property
    def in_progress(self) -> bool:
        return self._run_task is not None and not self._run_task.done()

    async def installed_versions(self) -> dict[str, str | None]:
        """Asks each town what it is running. Towns that are offline map to None."""
        channels = self.manager.channels
        sessions = await asyncio.gather(
            *(channel.get_query("select * from session", 5, suppress_timeout_error=True) for channel in channels),
            return_exceptions=True,
        )
        return {
            channel.channel_name: session.get("gameversion") or None if isinstance(session, dict) else None
            for channel, session in zip(channels, sessions)
        }

    def start(self, release: GameRelease, seconds: int, report: Report) -> bool:
        """Runs the update in the background. Returns False if one is already running."""
        if self.in_progress:
            return False
        self.holding_scrolls = True
        self._run_task = asyncio.create_task(self._run(release, seconds, report))
        return True

    def cancel(self) -> bool:
        """Cancels the update, unless it is too late because a town is going down."""
        if not self.in_progress:
            return False
        if any(task.get_status() == RestartStatus.RESTARTING for task in self._restart_tasks):
            return False
        self._run_task.cancel()
        return True

    async def _run(self, release: GameRelease, seconds: int, report: Report) -> None:
        async def say(message: str) -> None:
            try:
                await report(message)
            except Exception as e:
                logger.error(f"Failed to report on the game update: {e}", exc_info=True)

        work_folder = Path(tempfile.mkdtemp(prefix="ravenfall-update-"))
        try:
            await self._update(release, seconds, work_folder, say)
        except asyncio.CancelledError:
            logger.info("Game update cancelled")
        except Exception as e:
            logger.error(f"Game update failed: {e}", exc_info=True)
            await say(f"Game update failed: {e}")
        finally:
            self.holding_restarts = False
            self.holding_scrolls = False
            self._restart_tasks = []
            self.status = "idle"
            await asyncio.to_thread(shutil.rmtree, work_folder, ignore_errors=True)

    def _paths(self) -> tuple[Path, Path, list[Path]]:
        game_folder = os.getenv("RAVENFALL_FOLDER")
        if not game_folder or not Path(game_folder).is_dir():
            raise GameUpdateError("RAVENFALL_FOLDER is not set to a folder")
        ravenbot_folder = os.getenv("RAVENBOT_FOLDER")
        if not ravenbot_folder or not Path(ravenbot_folder).is_dir():
            raise GameUpdateError("RAVENBOT_FOLDER is not set to a folder")
        sandboxed_folder = os.getenv("RAVENFALL_SANDBOXED_FOLDER", "")
        box_folders = [
            Path(sandboxed_folder.replace("{box}", channel.sandboxie_box))
            for channel in self.manager.channels
            if sandboxed_folder and channel.sandboxie_box
        ]
        return Path(game_folder), Path(ravenbot_folder), box_folders

    def _ravenbot_channels(self) -> list[RFChannel]:
        """One channel per box, since each box runs one RavenBot."""
        return list({channel.sandboxie_box: channel for channel in self.manager.channels}.values())

    async def _start_ravenbots(self, say: Report) -> None:
        channels = self._ravenbot_channels()
        results = await asyncio.gather(
            *(channel.start_ravenbot() for channel in channels), return_exceptions=True
        )
        failed = [
            channel.channel_name
            for channel, result in zip(channels, results)
            if isinstance(result, Exception) or result[0] != 0
        ]
        if failed:
            logger.error(f"RavenBot failed to start for: {failed}")
            await say(f"RavenBot failed to start for: {', '.join(failed)}")

    async def _update(self, release: GameRelease, seconds: int, work_folder: Path, say: Report) -> None:
        game_folder, ravenbot_folder, box_folders = self._paths()
        game_archive = work_folder / GAME_ASSET
        ravenbot_archive = work_folder / RAVENBOT_ASSET
        extracted = work_folder / "extracted"
        ravenbot_extracted = work_folder / "extracted-ravenbot"
        ravenbot_files = ravenbot_extracted / "RavenBot"

        self.status = "downloading"
        folders = [*box_folders, game_folder]
        if not await asyncio.to_thread(copy_predownloaded_update, release, folders, game_archive):
            logger.info(f"Downloading Ravenfall {release.version} from {release.game.url}")
            await download_asset(release.game, game_archive)
        await download_asset(release.ravenbot, ravenbot_archive)

        self.status = "unpacking"
        await extract_archive(game_archive, extracted, (GAME_EXE, "Ravenfall_Data"))
        await extract_archive(ravenbot_archive, ravenbot_extracted, (f"RavenBot/{RAVENBOT_EXE}",))

        install_error: Exception | None = None

        async def install() -> None:
            nonlocal install_error
            self.status = "installing"
            logger.info(f"Installing Ravenfall {release.version} to {game_folder}")
            try:
                await asyncio.gather(*(channel.stop_ravenbot() for channel in self._ravenbot_channels()))
                # Everything is checked first, so that a locked file changes nothing
                await wait_for_unlocked_files(extracted, game_folder)
                await wait_for_unlocked_files(ravenbot_files, ravenbot_folder)
                count = await asyncio.to_thread(install_files, extracted, game_folder, box_folders)
                await asyncio.to_thread(install_ravenbot, ravenbot_files, ravenbot_folder)
                logger.info(f"Installed {count} files and {RAVENBOT_EXE}")
            except Exception as e:
                logger.error(f"Failed to install the update: {e}", exc_info=True)
                install_error = e
            await self._start_ravenbots(say)

        self.status = "waiting for dungeons and raids to finish"
        await self._wait_for_events_to_finish(say)

        self.status = "waiting for towns"
        self.holding_restarts = True
        await self.finish_running_restarts()

        gate = TownGate(install)
        tasks = self._queue_restarts(release, seconds, gate)
        try:
            await asyncio.wait([task.waiting_task for task in tasks])
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise

        if gate.abandoned:
            raise GameUpdateError("Not every town stopped, so nothing was installed")
        if install_error:
            raise GameUpdateError(f"The install failed, check that the game still works: {install_error}")

        installed = await self.installed_versions()
        behind = {
            name: version or "offline"
            for name, version in installed.items()
            if normalize_version(version or "") != normalize_version(release.version)
        }
        if behind:
            towns = ", ".join(f"{name} ({version})" for name, version in behind.items())
            await say(f"Installed Ravenfall {release.version}, but these towns aren't running it: {towns}")
        else:
            await say(f"Ravenfall and RavenBot have been updated to {release.version}!")

    async def _wait_for_events_to_finish(self, say: Report) -> None:
        clear_since = None
        announced = False
        while True:
            if any(has_event(channel) for channel in self.manager.channels):
                clear_since = None
                if not announced:
                    await say("The update is ready. Scrolls are paused until it's done, waiting for dungeons and raids to finish...")
                    announced = True
            elif clear_since is None:
                clear_since = time.monotonic()
            elif time.monotonic() - clear_since >= EVENTS_SETTLE_TIME:
                return
            await asyncio.sleep(EVENT_POLL_INTERVAL)

    async def finish_running_restarts(self) -> None:
        running: list[RFRestartTask] = []
        for channel in self.manager.channels:
            task = channel.restart_task
            if task is None or task.finished():
                continue
            if task.get_status() == RestartStatus.RESTARTING:
                running.append(task)
            else:
                task.cancel()
        if not running:
            return

        await asyncio.wait([task.waiting_task for task in running], timeout=RUNNING_RESTART_TIMEOUT)
        stuck = [task.waiting_task for task in running if not task.waiting_task.done()]
        for task in running:
            task.cancel()
        if stuck:
            await asyncio.wait(stuck)

    def _queue_restarts(self, release: GameRelease, seconds: int, gate: TownGate) -> list[RFRestartTask]:
        channels: list[RFChannel] = self.manager.channels
        tasks = [
            channel.queue_restart(
                seconds,
                label=f"Updating to version {release.version}",
                reason=RestartReason.GAME_UPDATE,
                before_start=gate.town_stopped,
            )
            for channel in channels
        ]
        gate.tasks = tasks
        self._restart_tasks = tasks
        for task in tasks:
            task.peers = [peer for peer in tasks if peer is not task]
            task.before_stop = gate.town_ready
        return tasks
