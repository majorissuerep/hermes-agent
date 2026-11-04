"""Native E2E processes must resolve the checkout's actual build identity."""

from hermes_cli.version_info import _reset_version_info_cache

_reset_version_info_cache()


def pytest_addoption(parser):
    parser.addoption("--formal-checker-store", default=None,
                     help="Explicit PM runtime store for native formal-checker qualification")
