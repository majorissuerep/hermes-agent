"""Decode the generated-program transport before fake backends inspect its script."""

import shlex


def bash_program(command: str) -> str:
    argv = shlex.split(command)
    assert len(argv) == 3 and argv[:2] == ["bash", "-c"], command
    return argv[2]
