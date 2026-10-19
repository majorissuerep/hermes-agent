"""Private stdio runtime owned by the terminal renderer."""
import hermes_bootstrap

# Node launches store Python, not a dependency-generation interpreter. Activate the
# owning install before key adoption imports crypto, without consuming its sealed FD.
hermes_bootstrap.harden_import_path()

from hermes_security.handoff import adopt_inherited_key

adopt_inherited_key()

from agent.code_skew import record_boot_fingerprint

record_boot_fingerprint()

from tui_runtime.entry import main

main()
