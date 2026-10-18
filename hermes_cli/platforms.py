"""Terminal execution surfaces offered by local setup."""
from collections import OrderedDict
from typing import NamedTuple

class PlatformInfo(NamedTuple):
    label: str
    default_toolset: str

PLATFORMS = OrderedDict([
    ("cli", PlatformInfo("Terminal", "hermes-cli")),
    ("cron", PlatformInfo("Scheduled terminal task", "hermes-cron")),
])

def platform_label(key: str, default: str = "") -> str:
    info = PLATFORMS.get(key)
    return info.label if info else default

def get_all_platforms():
    return OrderedDict(PLATFORMS)
