"""Timestamp child log lines without depending on an interface or supervisor."""

from datetime import datetime
import re

_TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}(?:\s|$)")


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:23]


def stamp_line(line: str) -> str:
    rendered = line.rstrip("\r\n")
    prefix = "" if _TIMESTAMP_PREFIX.match(rendered) else f"{timestamp()} "
    return f"{prefix}{rendered}\n"
