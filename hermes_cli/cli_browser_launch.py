"""Protected local browser launch and concise slash-command diagnostics."""
from __future__ import annotations

import time

from hermes_cli.browser_connect import (
    discover_local_cdp_url, find_free_debug_port, launch_chrome_debug,
    local_port_in_use, manual_chrome_debug_command)
from hermes_cli.cli_commands_session_tools import _pr, _t


def launch_default_cdp_browser(port: int):
    """Launch a local debug browser for the default CDP URL; returns the discovered CDP url or
    None (after printing what went wrong / how to launch manually)."""
    import platform as _plat
    launch_port = port
    if local_port_in_use(port):
        launch_port = find_free_debug_port(port)
        _pr(f"   {_t('browser.port_occupied', port=port)}",
            f"     {_t('browser.port_occupied_detail', launch_port=launch_port)}")
    else:
        print(f"   {_t('browser.launching')}")
    launch = launch_chrome_debug(launch_port, _plat.system())
    if not launch.launched:
        print(f"   {_t('browser.launch_failed')}")
        if launch.hint:
            print(f"     {launch.hint}")
        if launch.profile_error:  # the protected debug profile could not be prepared: offer no bypass
            return None
        chrome_cmd = manual_chrome_debug_command(launch_port, _plat.system(), launch.profile_dir)
        if chrome_cmd:
            _pr(f"     {_t('browser.launch_manually')}", f"     {chrome_cmd}")
        else:
            print(f"     {_t('browser.no_executable')}")
        return None
    for _wait in range(10):  # wait for the DevTools discovery endpoint to come up
        found = discover_local_cdp_url(launch_port, timeout=1.0)
        if found:
            print(f"   {_t('browser.launched', port=launch_port)}")
            return found
        time.sleep(0.5)
    _pr(f"   {_t('browser.not_responding', port=launch_port)}", f"     {_t('browser.try_again')}")
    return None
