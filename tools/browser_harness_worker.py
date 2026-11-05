"""First-party entry point for an already-admitted private Browser Harness request."""
from pathlib import Path
import sys


def command_for_browser(command, *, private):
    """Retain the selected PM interpreter and SDK entry point for shared browsers."""
    return [command[0], str(Path(__file__).resolve())] if private else command


def main():
    # Keep the SDK's normal import order (including its existing config/auth setup).
    from browser_harness import run
    from browser_harness import helpers
    from tools.browser_harness_screenshot import install_private_capture

    install_private_capture(helpers)
    run.capture_screenshot = helpers.capture_screenshot
    # SDK user code executes in run's globals, as it did under python -m. Keep
    # __name__, import __main__, __file__ and argv[0] attached to that same module.
    run.__name__ = '__main__'
    sys.modules['__main__'] = run
    sys.argv[0] = run.__file__
    return run.main()


if __name__ == '__main__':
    # SDK-site PYTHONPATH intentionally excludes unrelated caller modules. Resolve only this
    # installed first-party source tree, including relocated product payloads, before importing it.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
