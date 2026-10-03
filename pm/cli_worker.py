"""Stream a PM command while its owning process handles encrypted state."""
from contextlib import redirect_stdout
from io import TextIOBase


def run_cli(argv, output):
    from pm.cli import main

    class Output(TextIOBase):
        def write(self, text):
            output(text)
            return len(text)

        def flush(self):
            pass

    with redirect_stdout(Output()):
        return main(argv)
