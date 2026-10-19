"""Stdlib-only child tree, independent of the test runner's import path."""

from pathlib import Path
import subprocess
import sys
import threading
import time


def child(gate):
    subprocess.Popen([sys.executable, __file__, "writer", str(gate)], stdin=subprocess.DEVNULL)
    deadline = time.monotonic() + 15
    pause = threading.Event()
    while not Path(str(gate) + "-exit").exists() and time.monotonic() < deadline:
        pause.wait(0.01)


def writer(gate):
    deadline = time.monotonic() + 15
    pause = threading.Event()
    while not Path(str(gate) + "-stop").exists() and time.monotonic() < deadline:
        print(gate.name + "-output", flush=True)
        pause.wait(0.02)


if __name__ == "__main__":
    {"child": child, "writer": writer}[sys.argv[1]](Path(sys.argv[2]))
