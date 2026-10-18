"""Private stdio runtime owned by the terminal renderer."""
from hermes_security.handoff import adopt_inherited_key

adopt_inherited_key()

from agent.code_skew import record_boot_fingerprint

record_boot_fingerprint()

from tui_runtime.entry import main

main()
