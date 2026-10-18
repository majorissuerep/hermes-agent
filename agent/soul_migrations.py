"""Read-time cleanup of instructions inserted by retired interface versions."""

import re

_RETIRED_HEADING = "## Messaging other agents"
_RETIRED_SECTION = re.compile(
    r"\n*" + re.escape(_RETIRED_HEADING) + r"[ \t]*\n.*?(?=\n## |\Z)", re.DOTALL,
)


def strip_retired_soul_block(text: str) -> str:
    """Omit the old injected roster without modifying the user's stored SOUL file."""
    return _RETIRED_SECTION.sub("\n", text).strip() + "\n" if _RETIRED_HEADING in text else text
