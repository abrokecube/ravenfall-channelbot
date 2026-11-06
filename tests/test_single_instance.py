"""Run with: python -m unittest tests.test_single_instance"""

import subprocess
import sys
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRY_ACQUIRE = """
import sys
from utils.single_instance import acquire_single_instance
print(acquire_single_instance(sys.argv[1]), flush=True)
sys.stdin.read()
"""


class SingleInstance(unittest.TestCase):
    def setUp(self):
        self.name = f"ravenfall-channelbot-test-{uuid.uuid4()}"

    def start_copy(self) -> tuple[subprocess.Popen, str]:
        """Starts a process that tries to take the lock, then holds on until killed."""
        proc = subprocess.Popen(
            [sys.executable, "-c", TRY_ACQUIRE, self.name],
            cwd=REPO_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop_copy, proc)
        return proc, proc.stdout.readline().strip()

    def stop_copy(self, proc: subprocess.Popen):
        proc.kill()
        proc.wait()
        proc.stdin.close()
        proc.stdout.close()

    def test_second_copy_is_refused_while_the_first_runs(self):
        _, first = self.start_copy()
        _, second = self.start_copy()
        self.assertEqual(first, "True")
        self.assertEqual(second, "False")

    def test_killed_copy_gives_up_the_lock(self):
        proc, first = self.start_copy()
        self.assertEqual(first, "True")
        self.stop_copy(proc)
        _, second = self.start_copy()
        self.assertEqual(second, "True")


if __name__ == "__main__":
    unittest.main()
